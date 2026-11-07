' 拉起 Docker Desktop（真正脱离父进程，不会被调用方的进程树回收）。
' 用途：① 开机自启（放启动目录）；② 需要时手动双击 / wscript 调用。
' 已经在跑就什么都不做，避免重复实例抢 GPUPersistentCache 导致 GPU 进程崩。
Option Explicit

Dim exe, sh, wmi, col

Set sh = CreateObject("WScript.Shell")
exe = sh.ExpandEnvironmentStrings("%ProgramFiles%\Docker\Docker\Docker Desktop.exe")

On Error Resume Next
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set col = wmi.ExecQuery("SELECT Name FROM Win32_Process WHERE Name = 'Docker Desktop.exe'")
On Error GoTo 0

If Not col Is Nothing Then
    If col.Count > 0 Then WScript.Quit 0   ' 已经在跑
End If

sh.CurrentDirectory = sh.ExpandEnvironmentStrings("%ProgramFiles%\Docker\Docker")
sh.Run """" & exe & """", 1, False
