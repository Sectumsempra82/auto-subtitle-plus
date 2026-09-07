using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;
#if GUI
using System.Windows.Forms;
using System.Drawing;
#endif

internal static class Launcher {
    [STAThread]
    private static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        var start = new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe"));
        start.Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + Path.Combine(root, "Start-Application.ps1") + "\"";
        start.UseShellExecute = false;
        string system = Environment.GetFolderPath(Environment.SpecialFolder.System);
        start.EnvironmentVariables["PSModulePath"] = Path.Combine(system, @"WindowsPowerShell\v1.0\Modules");
        start.EnvironmentVariables["PATH"] = system + ";" + Environment.GetEnvironmentVariable("SystemRoot");
        start.EnvironmentVariables["ASP_ARGUMENTS"] = Convert.ToBase64String(Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(args)));
#if GUI
        start.EnvironmentVariables["ASP_EDITION"] = "GUI";
        start.CreateNoWindow = true;
        start.RedirectStandardOutput = true;
        start.RedirectStandardError = true;
        Application.EnableVisualStyles();
        var form = new Form { Text = "Auto Subtitle Plus - Dependency Setup", Width = 660, Height = 360, StartPosition = FormStartPosition.CenterScreen };
        var output = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, Dock = DockStyle.Fill };
        var cancel = new Button { Text = "Cancel", Dock = DockStyle.Bottom, Height = 32 };
        form.Controls.Add(output);
        form.Controls.Add(cancel);
        var process = new Process { StartInfo = start, EnableRaisingEvents = true };
        int result = 1;
        bool finished = false;
        DataReceivedEventHandler receive = (sender, ev) => {
            if (ev.Data == null || form.IsDisposed) return;
            form.BeginInvoke((Action)(() => {
                if (ev.Data == "ASP_READY") form.Hide();
                else output.AppendText(ev.Data + Environment.NewLine);
            }));
        };
        process.OutputDataReceived += receive;
        process.ErrorDataReceived += receive;
        form.Shown += (sender, ev) => {
            try { process.Start(); process.BeginOutputReadLine(); process.BeginErrorReadLine(); }
            catch (Exception error) { output.AppendText(error.Message); finished = true; cancel.Text = "Close"; }
        };
        var timer = new Timer { Interval = 250 };
        timer.Tick += (sender, ev) => {
            if (finished) return;
            try { if (!process.HasExited) return; } catch (InvalidOperationException) { return; }
            process.WaitForExit();
            result = process.ExitCode;
            finished = true;
            if (result == 0) form.Close();
            else { form.Show(); cancel.Text = "Close"; }
        };
        form.FormClosing += (sender, ev) => {
            if (finished) return;
            try {
                var kill = new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "taskkill.exe"), "/PID " + process.Id + " /T /F");
                kill.UseShellExecute = false; kill.CreateNoWindow = true;
                using (var killer = Process.Start(kill)) killer.WaitForExit();
            } catch (InvalidOperationException) { }
        };
        cancel.Click += (sender, ev) => form.Close();
        timer.Start();
        Application.Run(form);
        timer.Dispose(); process.Dispose();
        return result;
#else
        start.EnvironmentVariables["ASP_EDITION"] = "CLI";
        try { using (var process = Process.Start(start)) { process.WaitForExit(); return process.ExitCode; } }
        catch (Exception error) { Console.Error.WriteLine(error.Message); return 1; }
#endif
    }
}
