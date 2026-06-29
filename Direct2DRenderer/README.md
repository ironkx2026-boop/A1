# Direct2D GPU PDF renderer helper

`GPU_001.pyw` looks for this executable:

```text
Direct2DRenderer\GpuPdfRenderer.exe
```

When the executable exists, the GUI calls it instead of MuPDF:

```powershell
GpuPdfRenderer.exe --input "C:\path\file.pdf" --output "C:\out" --dpi 300 --prefix "file_page_"
```

Expected output files:

```text
file_page_0001.png
file_page_0002.png
...
```

The intended native implementation is:

1. Load the PDF with the Windows PDF API.
2. Create a Direct3D/Direct2D hardware device.
3. Render each page through `IPdfRendererNative::RenderPageToDeviceContext`.
4. Save each Direct2D render target as PNG with WIC.

Build requirements:

- Visual Studio 2022 Build Tools
- Desktop development with C++
- Windows 10/11 SDK

You do not need these installed on the local OCR PC when using GitHub Actions.

## Build with GitHub Actions

1. Push this repository to GitHub.
2. Open the repository on GitHub.
3. Go to **Actions**.
4. Select **Build Direct2D PDF Renderer**.
5. Click **Run workflow**.
6. Download the `GpuPdfRenderer-windows-x64` artifact.
7. Copy `GpuPdfRenderer.exe` into this folder:

```text
C:\PROJECTS\OCR\Direct2DRenderer\GpuPdfRenderer.exe
```

After that, `GPU_001.pyw` will prefer this Direct2D helper automatically. If the helper is missing, the GUI falls back to MuPDF CPU rendering.
