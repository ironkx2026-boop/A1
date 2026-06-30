#include <windows.h>
#include <windows.data.pdf.interop.h>
#include <wincodec.h>
#include <d2d1_3.h>
#include <d3d11_4.h>
#include <dxgi1_6.h>
#include <winrt/Windows.Data.Pdf.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Storage.h>

#include <algorithm>
#include <cwctype>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <wrl/client.h>

using Microsoft::WRL::ComPtr;
namespace fs = std::filesystem;
namespace pdf = winrt::Windows::Data::Pdf;
namespace storage = winrt::Windows::Storage;

struct Options {
    std::wstring input;
    std::wstring output;
    std::wstring prefix;
    double dpi = 300.0;
};

struct RenderDevice {
    ComPtr<ID3D11Device> d3d;
    ComPtr<ID3D11DeviceContext> d3dContext;
    ComPtr<IDXGIDevice> dxgiDevice;
    ComPtr<ID2D1Factory3> d2dFactory;
    ComPtr<ID2D1Device2> d2dDevice;
    ComPtr<ID2D1DeviceContext2> d2dContext;
    ComPtr<IPdfRendererNative> pdfRenderer;
    ComPtr<IWICImagingFactory2> wicFactory;
};

struct TiffWriter {
    ComPtr<IWICStream> stream;
    ComPtr<IWICBitmapEncoder> encoder;
    std::wstring outputPath;
};

static void check(HRESULT hr, const char* message) {
    if (FAILED(hr)) {
        throw std::runtime_error(std::string(message) + " HRESULT=0x" + [] (HRESULT value) {
            char buffer[16]{};
            sprintf_s(buffer, "%08X", static_cast<unsigned int>(value));
            return std::string(buffer);
        }(hr));
    }
}

static std::wstring arg_value(int argc, wchar_t** argv, const wchar_t* name) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (_wcsicmp(argv[i], name) == 0) {
            return argv[i + 1];
        }
    }
    return L"";
}

static Options parse_args(int argc, wchar_t** argv) {
    Options options;
    options.input = arg_value(argc, argv, L"--input");
    options.output = arg_value(argc, argv, L"--output");
    options.prefix = arg_value(argc, argv, L"--prefix");
    auto dpiText = arg_value(argc, argv, L"--dpi");
    if (!dpiText.empty()) {
        options.dpi = std::wcstod(dpiText.c_str(), nullptr);
    }

    if (options.input.empty() || options.output.empty()) {
        throw std::runtime_error("Required arguments: --input <pdf> --output <folder-or-tif> --dpi <value> [--prefix <prefix>]");
    }
    if (options.dpi < 72.0 || options.dpi > 1200.0) {
        throw std::runtime_error("DPI must be between 72 and 1200.");
    }
    if (options.prefix.empty()) {
        options.prefix = fs::path(options.input).stem().wstring() + L"_";
    }
    return options;
}

static bool is_tiff_path(const fs::path& path) {
    auto ext = path.extension().wstring();
    std::transform(ext.begin(), ext.end(), ext.begin(), [](wchar_t ch) {
        return static_cast<wchar_t>(std::towlower(ch));
    });
    return ext == L".tif" || ext == L".tiff";
}

static std::wstring tiff_path(const Options& options) {
    fs::path output(options.output);
    if (is_tiff_path(output)) {
        return output.wstring();
    }
    return (output / (options.prefix + L"multipage.tif")).wstring();
}

static RenderDevice create_device() {
    RenderDevice device;

    UINT flags = D3D11_CREATE_DEVICE_BGRA_SUPPORT;
#if defined(_DEBUG)
    flags |= D3D11_CREATE_DEVICE_DEBUG;
#endif

    D3D_FEATURE_LEVEL featureLevels[] = {
        D3D_FEATURE_LEVEL_12_1,
        D3D_FEATURE_LEVEL_12_0,
        D3D_FEATURE_LEVEL_11_1,
        D3D_FEATURE_LEVEL_11_0,
    };
    D3D_FEATURE_LEVEL createdLevel{};

    auto hr = D3D11CreateDevice(
        nullptr,
        D3D_DRIVER_TYPE_HARDWARE,
        nullptr,
        flags,
        featureLevels,
        static_cast<UINT>(std::size(featureLevels)),
        D3D11_SDK_VERSION,
        &device.d3d,
        &createdLevel,
        &device.d3dContext);

#if defined(_DEBUG)
    if (hr == DXGI_ERROR_SDK_COMPONENT_MISSING) {
        flags &= ~D3D11_CREATE_DEVICE_DEBUG;
        hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, flags,
            featureLevels, static_cast<UINT>(std::size(featureLevels)), D3D11_SDK_VERSION,
            &device.d3d, &createdLevel, &device.d3dContext);
    }
#endif
    check(hr, "D3D11CreateDevice failed");

    D2D1_FACTORY_OPTIONS factoryOptions{};
    check(D2D1CreateFactory(
        D2D1_FACTORY_TYPE_SINGLE_THREADED,
        __uuidof(ID2D1Factory3),
        &factoryOptions,
        reinterpret_cast<void**>(device.d2dFactory.GetAddressOf())),
        "D2D1CreateFactory failed");

    check(device.d3d.As(&device.dxgiDevice), "Query IDXGIDevice failed");
    check(device.d2dFactory->CreateDevice(device.dxgiDevice.Get(), &device.d2dDevice), "Create D2D device failed");
    check(device.d2dDevice->CreateDeviceContext(D2D1_DEVICE_CONTEXT_OPTIONS_NONE, &device.d2dContext),
        "Create D2D device context failed");
    check(PdfCreateRenderer(device.dxgiDevice.Get(), &device.pdfRenderer), "PdfCreateRenderer failed");

    check(CoCreateInstance(CLSID_WICImagingFactory2, nullptr, CLSCTX_INPROC_SERVER,
        IID_PPV_ARGS(&device.wicFactory)), "Create WIC factory failed");

    return device;
}

static TiffWriter create_tiff_writer(RenderDevice& device, const std::wstring& outputPath) {
    fs::create_directories(fs::path(outputPath).parent_path());

    TiffWriter writer;
    writer.outputPath = outputPath;
    check(device.wicFactory->CreateStream(&writer.stream), "Create WIC stream failed");
    check(writer.stream->InitializeFromFilename(outputPath.c_str(), GENERIC_WRITE), "Open TIFF output failed");
    check(device.wicFactory->CreateEncoder(GUID_ContainerFormatTiff, nullptr, &writer.encoder),
        "Create TIFF encoder failed");
    check(writer.encoder->Initialize(writer.stream.Get(), WICBitmapEncoderNoCache), "Initialize TIFF encoder failed");
    return writer;
}

static void write_tiff_frame(RenderDevice& device, TiffWriter& writer, ID3D11Texture2D* texture, double dpi) {
    D3D11_TEXTURE2D_DESC desc{};
    texture->GetDesc(&desc);

    D3D11_TEXTURE2D_DESC stagingDesc = desc;
    stagingDesc.BindFlags = 0;
    stagingDesc.MiscFlags = 0;
    stagingDesc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    stagingDesc.Usage = D3D11_USAGE_STAGING;

    ComPtr<ID3D11Texture2D> staging;
    check(device.d3d->CreateTexture2D(&stagingDesc, nullptr, &staging), "Create staging texture failed");
    device.d3dContext->CopyResource(staging.Get(), texture);

    D3D11_MAPPED_SUBRESOURCE mapped{};
    check(device.d3dContext->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped), "Map staging texture failed");

    ComPtr<IWICBitmapFrameEncode> frame;
    ComPtr<IPropertyBag2> properties;

    try {
        check(writer.encoder->CreateNewFrame(&frame, &properties), "Create TIFF frame failed");
        check(frame->Initialize(properties.Get()), "Initialize TIFF frame failed");
        check(frame->SetSize(desc.Width, desc.Height), "Set TIFF frame size failed");
        check(frame->SetResolution(dpi, dpi), "Set TIFF frame resolution failed");

        WICPixelFormatGUID format = GUID_WICPixelFormat32bppBGRA;
        check(frame->SetPixelFormat(&format), "Set TIFF pixel format failed");
        check(frame->WritePixels(desc.Height, mapped.RowPitch, mapped.RowPitch * desc.Height,
            static_cast<BYTE*>(mapped.pData)), "Write TIFF pixels failed");
        check(frame->Commit(), "Commit TIFF frame failed");
    } catch (...) {
        device.d3dContext->Unmap(staging.Get(), 0);
        throw;
    }

    device.d3dContext->Unmap(staging.Get(), 0);
}

static void render_page_to_tiff(RenderDevice& device, TiffWriter& writer, pdf::PdfPage const& page,
    const Options& options, uint32_t pageIndex) {
    const auto pageSize = page.Size();
    const auto scale = options.dpi / 96.0;
    const UINT width = std::max<UINT>(1, static_cast<UINT>(std::ceil(pageSize.Width * scale)));
    const UINT height = std::max<UINT>(1, static_cast<UINT>(std::ceil(pageSize.Height * scale)));

    D3D11_TEXTURE2D_DESC textureDesc{};
    textureDesc.Width = width;
    textureDesc.Height = height;
    textureDesc.MipLevels = 1;
    textureDesc.ArraySize = 1;
    textureDesc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    textureDesc.SampleDesc.Count = 1;
    textureDesc.Usage = D3D11_USAGE_DEFAULT;
    textureDesc.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;

    ComPtr<ID3D11Texture2D> texture;
    check(device.d3d->CreateTexture2D(&textureDesc, nullptr, &texture), "Create render texture failed");

    ComPtr<IDXGISurface> surface;
    check(texture.As(&surface), "Query IDXGISurface failed");

    D2D1_BITMAP_PROPERTIES1 bitmapProps = D2D1::BitmapProperties1(
        D2D1_BITMAP_OPTIONS_TARGET | D2D1_BITMAP_OPTIONS_CANNOT_DRAW,
        D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED),
        static_cast<float>(options.dpi),
        static_cast<float>(options.dpi));

    ComPtr<ID2D1Bitmap1> target;
    check(device.d2dContext->CreateBitmapFromDxgiSurface(surface.Get(), &bitmapProps, &target),
        "Create D2D target bitmap failed");

    device.d2dContext->SetTarget(target.Get());
    device.d2dContext->BeginDraw();
    device.d2dContext->Clear(D2D1::ColorF(D2D1::ColorF::White));

    auto params = PdfRenderParams(
        D2D1::RectF(0.0f, 0.0f, 0.0f, 0.0f),
        width,
        height,
        D2D1::ColorF(D2D1::ColorF::White),
        TRUE);
    check(device.pdfRenderer->RenderPageToDeviceContext(page.as<IUnknown>().get(), device.d2dContext.Get(), &params),
        "Render PDF page failed");

    check(device.d2dContext->EndDraw(), "D2D EndDraw failed");
    device.d2dContext->SetTarget(nullptr);

    write_tiff_frame(device, writer, texture.Get(), options.dpi);
    std::wcout << L"page " << (pageIndex + 1) << L" -> " << writer.outputPath << std::endl;
}

int wmain(int argc, wchar_t** argv) {
    try {
        winrt::init_apartment(winrt::apartment_type::single_threaded);

        const auto options = parse_args(argc, argv);
        const auto outputPath = tiff_path(options);

        auto file = storage::StorageFile::GetFileFromPathAsync(options.input).get();
        auto document = pdf::PdfDocument::LoadFromFileAsync(file).get();
        auto device = create_device();
        auto writer = create_tiff_writer(device, outputPath);

        const uint32_t pageCount = document.PageCount();
        for (uint32_t pageIndex = 0; pageIndex < pageCount; ++pageIndex) {
            auto page = document.GetPage(pageIndex);
            render_page_to_tiff(device, writer, page, options, pageIndex);
            page.Close();
        }

        check(writer.encoder->Commit(), "Commit TIFF encoder failed");
        std::wcout << L"done " << pageCount << L" page(s) -> " << outputPath << std::endl;
        return 0;
    } catch (const winrt::hresult_error& error) {
        std::wcerr << L"WinRT error 0x" << std::hex << static_cast<uint32_t>(error.code())
                   << L": " << error.message().c_str() << std::endl;
    } catch (const std::exception& error) {
        std::cerr << "Error: " << error.what() << std::endl;
    }

    return 1;
}
