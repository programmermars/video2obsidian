' Runs at Windows login: finds Desktop\04_AI*\video2obsidian and starts it hidden.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
desk = sh.SpecialFolders("Desktop")
For Each f In fso.GetFolder(desk).SubFolders
  If Left(f.Name, 5) = "04_AI" Then
    p = f.Path & "\video2obsidian\start-hidden.vbs"
    If fso.FileExists(p) Then sh.Run "wscript """ & p & """", 0, False
  End If
Next
