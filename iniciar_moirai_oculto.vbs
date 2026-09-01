' Lanca o iniciar_moirai.bat com a janela do console totalmente escondida - o .bat em
' si ja sobe o processo real (moirai.main) escondido via pythonw, mas o CONSOLE DO
' PROPRIO .bat (cmd.exe processando o script) sempre aparece quando aberto direto
' (ex.: atalho da area de trabalho). Esse .vbs existe so pra esconder esse console
' tambem - mesmo padrao do iniciar_iris_oculto.vbs/iniciar_argus_oculto.vbs.
'
' O atalho da area de trabalho ("MOIRAI") aponta pra esse arquivo, nao pro .bat direto.

Set objShell = CreateObject("WScript.Shell")
Set oFso = CreateObject("Scripting.FileSystemObject")
strPastaAtual = oFso.GetParentFolderName(WScript.ScriptFullName)
objShell.Run """" & strPastaAtual & "\iniciar_moirai.bat""", 0, False
