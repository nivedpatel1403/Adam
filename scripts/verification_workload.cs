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
            string c2Url = "http://192.168.19.2:18080/" + marker + "/c2";
            string responseFile = @"C:\ADAM\" + marker + "_c2_response.txt";
            string script = "Start-Sleep -Seconds 15; $response = Invoke-WebRequest -UseBasicParsing -Uri '" + c2Url +
                "' -TimeoutSec 10; $body = if ($response.Content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($response.Content) } else { [string]$response.Content }; [IO.File]::WriteAllText('" + responseFile + "', $body)";
            var httpChild = Process.Start(new ProcessStartInfo(
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe"),
                "-NoProfile -NonInteractive -Command \"" + script + "\"") {
                UseShellExecute = false, CreateNoWindow = true
            });
            httpChild.WaitForExit();
            if (httpChild.ExitCode != 0) throw new Exception("Benign HTTP child failed");
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
