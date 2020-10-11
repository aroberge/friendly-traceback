echo off
REM Default is Python 3.8

if "%1"=="3.6" goto py_36
if "%1"=="3.7" goto py_37
if "%1"=="3.9" goto py_39
if "%1"=="3.10" goto py_310


:py_38
venv-friendly3.8\scripts\activate
goto end

:py_36
venv-friendly3.6\scripts\activate
goto end

:py_37
venv-friendly3.7\scripts\activate
goto end

:py_39
venv-friendly3.9\scripts\activate
goto end

:py_310
venv-friendly3.10\scripts\activate

:end
