using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Windows.Forms;

// Build with scripts/build-launcher.ps1. No keys are embedded in this executable.
class XiaochiLauncher : Form
{
    static readonly string Root = AppDomain.CurrentDomain.BaseDirectory;
    static readonly string Python = Path.Combine(Root, @"src\.venv\Scripts\python.exe");
    Process server;
    int port;
    bool opened;
    DateTime started;
    readonly Label status = new Label();
    readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    string Url { get { return "http://127.0.0.1:" + port; } }

    XiaochiLauncher()
    {
        Text = "小弛 · 系统启动";
        ClientSize = new Size(580, 245);
        Font = new Font("Microsoft YaHei UI", 10);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        var title = new Label { Text = "小弛政务业务辅助系统", Location = new Point(24, 20), AutoSize = true, Font = new Font(Font.FontFamily, 18, FontStyle.Bold) };
        Controls.Add(title);
        status.Location = new Point(24, 70); status.Size = new Size(530, 48); Controls.Add(status);
        AddButton("启动服务", 24, delegate { TryStart(); });
        AddButton("停止服务", 130, delegate { Stop(); status.Text = "服务已停止。"; });
        AddButton("进入系统", 236, delegate { if (server != null && !server.HasExited && Healthy()) Open(Url); });
        AddButton("接口密钥配置", 342, delegate { EditEnv(); });
        var note = new Label { Text = "接口密钥修改后须重新启动服务。关闭本窗口将停止服务。\n首次使用须配置 DeepSeek 密钥；启用朗读须另行配置 Fish 密钥。", Location = new Point(24, 176), Size = new Size(540, 55) };
        Controls.Add(note);
        timer.Interval = 700;
        timer.Tick += delegate {
            if (server == null) return;
            if (server.HasExited) { timer.Stop(); status.Text = "服务启动失败或已退出，请查阅运行日志：.local/launcher.log"; return; }
            if (!opened && Healthy()) { opened = true; status.Text = "运行中：" + Url; Open(Url); }
            else if (!opened && (DateTime.Now - started).TotalSeconds > 120) { Stop(); status.Text = "服务启动超时，请查阅运行日志：.local/launcher.log"; }
        };
        FormClosing += delegate { Stop(); };
        Shown += delegate { TryStart(); };
    }
    void AddButton(string text, int x, EventHandler handler)
    {
        var b = new Button { Text = text, Location = new Point(x, 122), Size = new Size(100, 36) };
        b.Click += handler; Controls.Add(b);
    }
    static void Open(string target) { Process.Start(new ProcessStartInfo(target) { UseShellExecute = true }); }
    void EditEnv()
    {
        EnsureEnv();
        Process.Start(new ProcessStartInfo("notepad.exe", "\"" + Path.Combine(Root, ".env") + "\"") { UseShellExecute = true });
    }
    static void EnsureEnv()
    {
        string path = Path.Combine(Root, ".env");
        if (!File.Exists(path)) File.Copy(Path.Combine(Root, ".env.example"), path);
    }
    void TryStart()
    {
        try { StartServer(); status.Text = "正在启动，请稍候……"; timer.Start(); }
        catch (Exception ex) { status.Text = "启动失败"; MessageBox.Show(ex.Message, "小弛启动器"); }
    }
    void StartServer()
    {
        if (server != null && !server.HasExited) return;
        if (!File.Exists(Python)) throw new Exception("未找到 Python 环境。请保持 EXE 位于仓库根目录，并按 Windows启动说明.txt 安装环境。");
        EnsureEnv();
        Directory.CreateDirectory(Path.Combine(Root, ".local"));
        // Pick a free local port; never stop or reuse someone else's service.
        port = 8000;
        while (port < 8100) {
            var listener = new TcpListener(IPAddress.Loopback, port);
            try { listener.Start(); break; } catch (SocketException) { port++; } finally { listener.Stop(); }
        }
        if (port == 8100) throw new Exception("8000–8099 端口均被占用。");
        var info = new ProcessStartInfo(Python, "-m src.main --host 127.0.0.1 --port " + port) {
            WorkingDirectory = Root, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
        };
        info.EnvironmentVariables["PYTHONUTF8"] = "1";
        info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
        info.EnvironmentVariables["NO_PROXY"] = "localhost,127.0.0.1,::1";
        info.EnvironmentVariables["no_proxy"] = "localhost,127.0.0.1,::1";
        server = new Process { StartInfo = info };
        server.OutputDataReceived += Log;
        server.ErrorDataReceived += Log;
        server.Start(); server.BeginOutputReadLine(); server.BeginErrorReadLine();
        opened = false; started = DateTime.Now;
    }
    static readonly object LogLock = new object();
    static void Log(object sender, DataReceivedEventArgs e)
    {
        if (e.Data == null) return;
        lock (LogLock) { try { File.AppendAllText(Path.Combine(Root, ".local/launcher.log"), e.Data + Environment.NewLine, Encoding.UTF8); } catch (IOException) {} }
    }
    bool Healthy()
    {
        try {
            var request = (HttpWebRequest)WebRequest.Create(Url + "/api/config");
            request.Proxy = null; request.Timeout = 500;
            using (var response = request.GetResponse())
            using (var reader = new StreamReader(response.GetResponseStream())) { return reader.ReadToEnd().Contains("quick_questions"); }
        } catch (WebException) { return false; }
    }
    void Stop()
    {
        timer.Stop();
        if (server != null) {
            try { if (!server.HasExited) { server.Kill(); server.WaitForExit(5000); } } catch (InvalidOperationException) {}
            server.Dispose(); server = null;
        }
    }
    [STAThread]
    static int Main(string[] args)
    {
        bool owned;
        using (var mutex = new Mutex(true, "Local\\XiaochiLauncher_" + Root.ToLowerInvariant().GetHashCode().ToString("X"), out owned)) {
            if (!owned) { MessageBox.Show("小弛启动器已经打开，请使用现有窗口。"); return 2; }
            Application.EnableVisualStyles();
            using (var app = new XiaochiLauncher()) {
                if (Array.IndexOf(args, "--self-test") >= 0) {
                    int result = 1;
                    try {
                        app.StartServer();
                        for (int i = 0; i < 120; i++) {
                            if (app.server.HasExited) break;
                            if (app.Healthy()) { result = 0; break; }
                            Thread.Sleep(500);
                        }
                    } finally { app.Stop(); }
                    File.WriteAllText(Path.Combine(Root, ".local/launcher-self-test.txt"), result == 0 ? "PASS: backend config endpoint ready; child process stopped." : "FAIL: backend did not become ready.");
                    return result;
                }
                Application.Run(app);
            }
            return 0;
        }
    }
}
