using System;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;

internal static class Harness {
    [STAThread]
    private static int Main() {
        string error = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "race-error.txt");
        AppDomain.CurrentDomain.UnhandledException += (sender, ev) => {
            File.WriteAllText(error, ev.ExceptionObject.ToString());
            Environment.Exit(97);
        };
        Application.ThreadException += (sender, ev) => {
            File.WriteAllText(error, ev.Exception.ToString());
            Environment.Exit(98);
        };
        bool recreated = false;
        DateTime started = DateTime.UtcNow;
        var timer = new System.Windows.Forms.Timer { Interval = 20 };
        timer.Tick += (sender, ev) => {
            if (Application.OpenForms.Count == 0) return;
            Form form = Application.OpenForms[0];
            if (!recreated && (DateTime.UtcNow - started).TotalMilliseconds > 600) {
                recreated = true;
                var flags = BindingFlags.Instance | BindingFlags.NonPublic;
                int state = (int)typeof(Control).GetField("STATE_RECREATE", BindingFlags.Static | BindingFlags.NonPublic).GetRawConstantValue();
                var setState = typeof(Control).GetMethod("SetState", flags);
                setState.Invoke(form, new object[] { state, true });
                typeof(Control).GetMethod("DestroyHandle", flags).Invoke(form, null);
                Thread.Sleep(1000);
                typeof(Control).GetMethod("CreateHandle", flags).Invoke(form, null);
                setState.Invoke(form, new object[] { state, false });
            }
            if ((DateTime.UtcNow - started).TotalSeconds > 15) {
                File.WriteAllText(error, "Launcher did not exit after its child process");
                Environment.Exit(96);
            }
        };
        timer.Start();
        int result = (int)typeof(Launcher).GetMethod("Run", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { new string[0] });
        timer.Dispose();
        return recreated ? result : 95;
    }
}
