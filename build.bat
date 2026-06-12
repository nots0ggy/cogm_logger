:: Build the logger
cd logger
CALL install.bat

:: Copy everything form logger/dist/logger to dist/cogm-logger/logger/
cd ..
xcopy logger\dist\logger dist\cogm-logger\logger\ /E /Y

:: Bundle the packet config so the status check finds it (live capture ignores it)
if exist config.ini copy /Y config.ini dist\cogm-logger\config.ini



:: Install Dependencines the Frontend
cd ui 
CALL npm i

CALL npm i -g @neutralinojs/neu@11.3.1

:: Compile the program
cd .. 
CALL neu update
CALL neu build

echo Build completed. Compiled files are in dist/cogm-logger/