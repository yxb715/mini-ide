' Silent launcher for mini-ide: no cmd window, GUI only
' Double-click this file to start mini-ide.
' Optional: pass a project path to open it on startup.
'   wscript mini-ide.vbs "C:\path\to\project"
Option Explicit

Dim shell, fso, scriptDir, here, args, extra, i, pythonw, entry, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
here = fso.GetParentFolderName(scriptDir)

' Build passthrough argument string (each arg wrapped in double quotes)
extra = ""
Set args = WScript.Arguments
For i = 0 To args.Count - 1
    extra = extra & " """ & args(i) & """"
Next

shell.CurrentDirectory = here
pythonw = fso.BuildPath(here, ".venv\Scripts\pythonw.exe")
entry = fso.BuildPath(here, "main.py")

If Not fso.FileExists(pythonw) Then
    MsgBox "Cannot find Python launcher: " & pythonw, vbCritical, "mini-ide"
    WScript.Quit 1
End If

cmd = """" & pythonw & """ """ & entry & """" & extra
shell.Run cmd, 0, False
