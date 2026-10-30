@echo off
cd /d "%~dp0"

if exist .git (
    attrib -r -s -h .git\* /s /d >nul 2>&1
    rmdir /s /q .git
)

git init -b main
git add -A
git commit -m "initial release"
