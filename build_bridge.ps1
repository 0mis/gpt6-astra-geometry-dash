param([switch]$Fresh)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolRoot = Join-Path $taskRoot 'vendor\toolchain'
$compilerBin = Join-Path $toolRoot 'llvm\clang+llvm-23.1.1-x86_64-pc-windows-msvc\bin'
$sysRoot = Join-Path $toolRoot 'winsysroot'
$vcRoot = Join-Path $sysRoot 'VC\Tools\MSVC\14.44.17.14'
$sdkRoot = Join-Path $sysRoot 'Windows Kits\10'
$env:GEODE_SDK = Join-Path $taskRoot 'vendor\geode-sdk'
$env:CPM_SOURCE_CACHE = Join-Path $taskRoot 'vendor\cpm-cache'
$env:LIB = (Join-Path $vcRoot 'lib\x64') + ';' + (Join-Path $sdkRoot 'Lib\10.0.26100\um\x64') + ';' + (Join-Path $sdkRoot 'Lib\10.0.26100\ucrt\x64')
$env:INCLUDE = (Join-Path $vcRoot 'include') + ';' + (Join-Path $sdkRoot 'Include\10.0.26100\ucrt') + ';' + (Join-Path $sdkRoot 'Include\10.0.26100\shared') + ';' + (Join-Path $sdkRoot 'Include\10.0.26100\um') + ';' + (Join-Path $sdkRoot 'Include\10.0.26100\winrt')
$env:PATH = $compilerBin + ';' + (Join-Path $taskRoot 'vendor\geode-tools\cli') + ';' + $env:PATH
$cmake = Join-Path $taskRoot 'vendor\build-python\cmake\data\bin\cmake.exe'
$arguments = @('-S', (Join-Path $taskRoot 'bridge'), '-B', (Join-Path $taskRoot 'build'), '-G', 'Ninja',
    '-DCMAKE_BUILD_TYPE=RelWithDebInfo', '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL',
    ('-DCMAKE_MAKE_PROGRAM=' + (Join-Path $taskRoot 'vendor\build-python\bin\ninja.exe')),
    ('-DCMAKE_C_COMPILER=' + (Join-Path $compilerBin 'clang-cl.exe')),
    ('-DCMAKE_CXX_COMPILER=' + (Join-Path $compilerBin 'clang-cl.exe')),
    ('-DCMAKE_LINKER=' + (Join-Path $compilerBin 'lld-link.exe')),
    ('-DCMAKE_AR=' + (Join-Path $compilerBin 'llvm-lib.exe')),
    ('-DCMAKE_RC_COMPILER=' + (Join-Path $compilerBin 'llvm-rc.exe')),
    ('-DCMAKE_MT=' + (Join-Path $compilerBin 'llvm-mt.exe')))
if ($Fresh) { $arguments += '--fresh' }
$arguments = @($arguments | ForEach-Object { $_.Replace('\', '/') })
& $cmake @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $cmake --build (Join-Path $taskRoot 'build') --parallel 4
exit $LASTEXITCODE
