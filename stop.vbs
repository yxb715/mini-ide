' 用 /T 选项级联杀掉 mini-ide.exe 及其启动的所有子进程
' （mini-ide 启动的 java/node 子进程会一起被清理，不会留孤儿）
Set sh = CreateObject("WScript.Shell")
sh.Run "taskkill /F /T /IM mini-ide.exe", 0, False
