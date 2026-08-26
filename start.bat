@echo off
chcp 65001 >nul
title 轻会议 LiteMeet
cd /d "%~dp0"

where java >nul 2>nul
if errorlevel 1 (
  echo [错误] 未检测到 Java，请先安装 JDK 21+（https://adoptium.net）
  pause
  exit /b 1
)

if not exist "backend\target\litemeet-backend.jar" (
  where mvn >nul 2>nul
  if errorlevel 1 (
    echo [错误] 后端未构建且未检测到 Maven，请安装 Maven 后重试
    pause
    exit /b 1
  )
  echo [首次运行] 正在构建后端，请稍候...
  call mvn -q -f backend\pom.xml -DskipTests package
  if errorlevel 1 (
    echo [错误] 后端构建失败，请检查 Maven 配置与网络
    pause
    exit /b 1
  )
)

rem ---- 清理上次运行残留的服务进程，避免端口占用导致启动失败 ----
echo 正在检查残留进程...
powershell -NoProfile -Command "$cleaned=0; foreach($port in 5678,5679,3000,3001,7880){ $c=Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue; if($c){ $procId=($c|Select-Object -First 1).OwningProcess; $proc=Get-Process -Id $procId -ErrorAction SilentlyContinue; $name=if($proc){$proc.ProcessName}else{'unknown'}; if($name -eq 'java' -or $name -eq 'livekit-server'){ Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue; Write-Host ('  已停止残留服务进程 PID '+$procId+'（端口 '+$port+'）'); $cleaned++ } else { Write-Host ('  [警告] 端口 '+$port+' 被其他程序 '+$name+'（PID '+$procId+'）占用，请手动处理') } } }; if($cleaned -gt 0){ Start-Sleep -Milliseconds 800 }"

rem ---- 构建前端（Vite 多页应用）：frontend/src → frontend/dist，保证启动的是最新构建 ----
echo 正在构建前端（Vite MPA）...
where npm >nul 2>nul
if errorlevel 1 (
  echo [错误] 未检测到 Node.js/npm。请先安装 Node.js，并打开 frontend 目录执行 npm install
  pause
  exit /b 1
)
if not exist "frontend\node_modules" (
  echo [首次运行] 正在安装前端依赖（npm install），请稍候...
  pushd frontend
  call npm install
  if errorlevel 1 (
    popd
    echo [错误] 前端依赖安装失败，请检查网络后重试
    pause
    exit /b 1
  )
  popd
)
pushd frontend
call npm run build
set "BUILD_ERR=%errorlevel%"
popd
if not "%BUILD_ERR%"=="0" (
  echo [错误] 前端构建失败，服务未启动。请检查 frontend 源码后重试
  pause
  exit /b 1
)
echo 前端构建完成。

echo 正在启动 LiveKit SFU 媒体服务器...
start "LiteMeet-LiveKit" /min cmd /c "livekit\livekit-server.exe --config livekit\livekit.yaml"

echo 正在启动后端 API 服务...
start "LiteMeet-Backend" /min cmd /c "java -jar backend\target\litemeet-backend.jar"

echo 正在启动前端服务...
start "LiteMeet-Frontend" /min cmd /c "java tools\FrontendServer.java --root frontend\dist --http 3000 --https 3001 --keystore data\https-keystore.p12"

rem ---- 轮询等待服务真正就绪后再打开浏览器（替代固定延时） ----
echo 等待服务就绪...
powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 50;$i++){ Start-Sleep -Milliseconds 500; try{ $null=Invoke-WebRequest -Uri 'http://localhost:3000' -UseBasicParsing -TimeoutSec 1; $null=Invoke-WebRequest -Uri 'http://localhost:5678/api/records' -UseBasicParsing -TimeoutSec 1; $null=Get-NetTCPConnection -LocalPort 7880 -State Listen -ErrorAction Stop; $ok=$true; break }catch{} }; if($ok){ exit 0 } else { exit 1 }"
if errorlevel 1 (
  echo [提示] 服务仍在启动中，稍后请手动访问 http://localhost:3000
) else (
  start "" http://localhost:3000
)

echo ========================================
echo   轻会议 LiteMeet 已启动
echo ----------------------------------------
echo   本机使用:   http://localhost:3000   （推荐，功能完整）
echo   后端 API:   http://localhost:5678
echo   SFU 媒体:   ws://localhost:7880  （LiveKit 转发音视频，支撑多人会议）
echo   接口文档:   http://localhost:5678/swagger-ui.html
echo               （多前端对接后端看这里）
echo   局域网设备（音视频完整，单端口同源，首次访问需信任证书）:
powershell -NoProfile -Command "$ips=@(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' } | Select-Object -ExpandProperty IPAddress -Unique); foreach($ip in $ips){ Write-Host ('              https://'+$ip+':5679') }"
echo ========================================
echo 关闭 LiteMeet-LiveKit / LiteMeet-Backend / LiteMeet-Frontend 三个窗口即停止服务
echo 重复运行本脚本会自动清理旧进程并重启
echo.
pause
