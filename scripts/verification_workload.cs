// Benign VM acceptance workload. Arguments identify the cycle and local test server.
using System;
using System.IO;
using System.Net;
using System.Diagnostics;
using System.Threading;
using Microsoft.Win32;

class VerificationWorkload {
    static int Main() {
        string marker = Path.GetFileNameWithoutExtension(Environment.GetCommandLineArgs()[0]);
        string cycle = marker.Contains("CYCLE_1_") ? "1" : "2";
        try {
            Thread.Sleep(3000);
            Directory.CreateDirectory(@"C:\ADAM");
            File.WriteAllText(@"C:\ADAM\run_marker_" + cycle + ".txt", marker);
            using (var key = Registry.LocalMachine.CreateSubKey(@"Software\ADAM\Cycle" + cycle)) {
                key.SetValue("Marker", marker);
            }
            var child = Process.Start(new ProcessStartInfo("whoami.exe") {
                UseShellExecute = false, CreateNoWindow = true
            });
            child.WaitForExit();
            for (int i = 0; i < 3; i++) {
                var request = (HttpWebRequest)WebRequest.Create("http://192.168.19.2:18080/" + marker);
                request.Proxy = null;
                request.Timeout = 10000;
                using (var response = request.GetResponse()) {}
                Thread.Sleep(2000);
            }
            Thread.Sleep(10000);
            return 0;
        } catch (Exception error) {
            Console.Error.WriteLine(error.Message);
            return 1;
        }
    }
}
