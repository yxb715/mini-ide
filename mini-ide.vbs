' Silent launcher for mini-ide: no cmd window, GUI only
' Double-click this file to start mini-ide.
' Optional: pass a project path to open it on startup.
'   wscript mini-ide.vbs "C:\path\to\project"
Option Explicit

Dim shell, fso, here, args, extra, i
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)

' Build passthrough argument string (each arg wrapped in double quotes)
extra = ""
Set args = WScript.Arguments
For i = 0 To args.Count - 1
    extra = extra & " """ & args(i) & """"
Next

shell.CurrentDirectory = here
shell.Run ".venv\Scripts\mini-ide.exe main.py" & extra, 0, False
