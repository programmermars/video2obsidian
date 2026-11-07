' Start Video2Obsidian in the background (no console window).
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
d = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = d
Set env = sh.Environment("Process")
env("V2O_NO_BROWSER") = "1"
env("V2O_WAIT_PORT") = "1"
env("PYTHONUTF8") = "1"
sh.Run """" & d & "\.venv\Scripts\pythonw.exe"" """ & d & "\app.py""", 0, False
