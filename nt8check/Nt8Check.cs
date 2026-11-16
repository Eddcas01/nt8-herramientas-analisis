// Verificador de compilacion de NinjaScript: usa el MISMO Roslyn y las MISMAS referencias que NT8.
// Compilar con el csc del Framework (C# 5). Uso: nt8check.exe [-w] archivo1.cs archivo2.cs ...
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.Text;

public static class Nt8Check
{
    const string Nt = @"C:\Program Files\NinjaTrader 8\bin";
    static int exitCode = 0;

    public static int Main(string[] args)
    {
        // Roslyn 5 de NT pide versiones exactas de System.Memory / Immutable: se resuelven por nombre desde la carpeta de NT.
        AppDomain.CurrentDomain.AssemblyResolve += delegate(object s, ResolveEventArgs e)
        {
            string n = e.Name.Split(',')[0];
            string p = Path.Combine(Nt, n + ".dll");
            return File.Exists(p) ? Assembly.LoadFrom(p) : null;
        };
        bool warnings = args.Contains("-w");
        List<string> files = args.Where(a => a != "-w").ToList();
        // Hilo con pila grande: Roslyn desborda 1MB con algunos errores (identificador inexistente, expresiones profundas).
        Thread t = new Thread(delegate() { exitCode = Run(files, warnings); }, 512 * 1024 * 1024);
        t.Start();
        t.Join();
        return exitCode;
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    static int Run(List<string> files, bool warnings)
    {
        string custom = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), @"NinjaTrader 8\bin\Custom");
        string fw = @"C:\Windows\Microsoft.NET\Framework64\v4.0.30319";
        string wpf = Path.Combine(fw, "WPF");
        string[] refPaths =
        {
            fw + @"\mscorlib.dll", fw + @"\System.dll", fw + @"\System.Core.dll", fw + @"\System.ComponentModel.DataAnnotations.dll",
            fw + @"\System.Xaml.dll", fw + @"\System.Xml.dll", fw + @"\System.Xml.Linq.dll", fw + @"\Microsoft.CSharp.dll",
            fw + @"\System.Windows.Forms.dll", fw + @"\System.Net.Http.dll", fw + @"\System.Web.Extensions.dll",
            fw + @"\System.Drawing.dll", fw + @"\System.Runtime.Serialization.dll",
            wpf + @"\WindowsBase.dll", wpf + @"\PresentationCore.dll", wpf + @"\PresentationFramework.dll", wpf + @"\UIAutomationProvider.dll",
            Nt + @"\NinjaTrader.Core.dll", Nt + @"\NinjaTrader.Gui.dll", Nt + @"\SharpDX.dll", Nt + @"\SharpDX.Direct2D1.dll",
            Nt + @"\InfragisticsWPF.DataPresenter.dll", Nt + @"\Infralution.Localization.Wpf.dll",
            custom + @"\NinjaTrader.Custom.dll", custom + @"\Newtonsoft.Json.dll"
            // (NinjaTrader.Vendor.dll duplica Strategy/EMA/Draw con Custom: no se referencia)
        };
        List<MetadataReference> refs = new List<MetadataReference>();
        foreach (string p in refPaths)
        {
            if (File.Exists(p)) refs.Add(MetadataReference.CreateFromFile(p));
            else Console.WriteLine("AVISO: referencia no encontrada: " + p);
        }

        CSharpParseOptions parse = new CSharpParseOptions(LanguageVersion.Latest);
        int rc = 0;
        foreach (string f in files)
        {
            string full = Path.GetFullPath(f);
            string leaf = Path.GetFileName(full);
            SyntaxTree tree = CSharpSyntaxTree.ParseText(File.ReadAllText(full), parse, full);
            CSharpCompilation comp = CSharpCompilation.Create("NT8Check", new SyntaxTree[] { tree }, refs,
                new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary));
            int errs = 0, warns = 0;
            foreach (Diagnostic d in comp.GetDiagnostics())
            {
                if (d.Severity == DiagnosticSeverity.Error) errs++;
                else if (d.Severity == DiagnosticSeverity.Warning) warns++;
                if (d.Severity == DiagnosticSeverity.Error || (warnings && d.Severity == DiagnosticSeverity.Warning))
                {
                    LinePosition ls = d.Location.GetLineSpan().StartLinePosition;
                    Console.WriteLine("{0}({1},{2}): {3} {4}: {5}", leaf, ls.Line + 1, ls.Character + 1, d.Severity, d.Id, d.GetMessage());
                }
            }
            Console.WriteLine("== {0}: {1} errores, {2} avisos", leaf, errs, warns);
            if (errs > 0) rc = 1;
        }
        return rc;
    }
}
