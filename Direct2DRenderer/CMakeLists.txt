cmake_minimum_required(VERSION 3.24)

project(GpuPdfRenderer LANGUAGES CXX)

add_executable(GpuPdfRenderer
    src/main.cpp
)

add_executable(GPUpdfTIF
    src/tif_main.cpp
)

target_compile_features(GpuPdfRenderer PRIVATE cxx_std_20)
target_compile_features(GPUpdfTIF PRIVATE cxx_std_20)

target_compile_definitions(GpuPdfRenderer PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
)
target_compile_definitions(GPUpdfTIF PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
)

target_link_libraries(GpuPdfRenderer PRIVATE
    windowsapp
    runtimeobject
    Windows.Data.Pdf
    d2d1
    d3d11
    dxgi
    windowscodecs
    ole32
)

target_link_libraries(GPUpdfTIF PRIVATE
    windowsapp
    runtimeobject
    Windows.Data.Pdf
    d2d1
    d3d11
    dxgi
    windowscodecs
    ole32
)
