Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = dir
pyw = dir & "\.venv\Scripts\pythonw.exe"
If fso.FileExists(pyw) Then
    WshShell.Run """" & pyw & """ """ & dir & "\ui.py""", 0, False
Else
    WshShell.Run "pythonw """ & dir & "\ui.py""", 0, False
End If
