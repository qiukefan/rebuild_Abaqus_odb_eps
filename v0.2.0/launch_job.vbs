Option Explicit
' v0.2.0: launch the bundled worker invisibly. No terminal window or shell commands.
Dim fs, sh, base, requestPath, jobDir, python, worker, stream, requestText, rx, matches, command
Set fs = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fs.GetParentFolderName(WScript.ScriptFullName)
If WScript.Arguments.Count <> 1 Then WScript.Quit 2
requestPath = fs.GetAbsolutePathName(WScript.Arguments(0))
jobDir = fs.GetParentFolderName(requestPath)
If Not fs.FileExists(requestPath) Then WScript.Quit 2
python = fs.BuildPath(base, "runtime\pythonw.exe")
worker = fs.BuildPath(base, "backend\worker.py")

On Error Resume Next
Set stream = CreateObject("ADODB.Stream")
stream.Type = 2
stream.Charset = "utf-8"
stream.Open
stream.LoadFromFile requestPath
requestText = stream.ReadText
stream.Close
If Err.Number <> 0 Then
    Err.Clear
    WriteError "Cannot read request JSON."
    WScript.Quit 2
End If
On Error GoTo 0

' Only the optional executable path is read here; all other fields go to Python's JSON parser.
Set rx = New RegExp
rx.Pattern = """pythonExecutable""\s*:\s*""([^""]*)"""
rx.Global = False
Set matches = rx.Execute(requestText)
If matches.Count > 0 Then
    If Len(matches(0).SubMatches(0)) > 0 Then
        python = Replace(matches(0).SubMatches(0), "\\", "\")
        python = Replace(python, "\/", "/")
    End If
End If
If Not fs.FileExists(python) Then
    WriteError "Bundled Python is missing. Extract the complete v0.2.0 package."
    WScript.Quit 2
End If
If Not fs.FileExists(worker) Then
    WriteError "Worker is missing. Extract the complete v0.2.0 package."
    WScript.Quit 2
End If
If InStr(python, """") > 0 Or InStr(requestPath, """") > 0 Then
    WriteError "Invalid executable or request path."
    WScript.Quit 2
End If
command = Quote(python) & " " & Quote(worker) & " --request " & Quote(requestPath)
On Error Resume Next
sh.Run command, 0, False
If Err.Number <> 0 Then
    Err.Clear
    WriteError "Could not launch the background worker."
    WScript.Quit 2
End If
On Error GoTo 0

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function

Sub WriteError(message)
    Dim out
    Set out = CreateObject("ADODB.Stream")
    out.Type = 2
    out.Charset = "utf-8"
    out.Open
    out.WriteText "{""state"":""error"",""progress"":0,""message"":""" & message & """,""error"":""" & message & """}"
    out.SaveToFile fs.BuildPath(jobDir, "status.json"), 2
    out.Close
End Sub
