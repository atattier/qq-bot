' One-click: start the qq-bot keeper guard silently (no window, no UAC).
' The guard checks every 5 min and restarts keeper if it dies; the new
' _keeper.py also watches the guard (ensure_guard_running), so they keep
' each other alive. Double-click this file. If a guard is already running
' it exits by itself (no double-guard).
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "pythonw.exe -u """ & sh.CurrentDirectory & "\tools\keeper_guard.py"" --loop", 0, False
