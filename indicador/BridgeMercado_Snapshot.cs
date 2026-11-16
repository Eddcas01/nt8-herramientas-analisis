// BridgeMercado_Snapshot.cs
//
// INDICADOR "puente": escribe un snapshot del mercado a un archivo JSON cada N segundos, para que
// un conector MCP (Python) lo lea y Claude pueda analizar el mercado EN VIVO junto con vos, sin que
// tengas que exportar ni pegar nada.
//
// Que escribe (snapshot.json):
//   - ts, instrumento, timeframe
//   - precio actual, EMA rapida, EMA lenta, tendencia (ema_rapida vs ema_lenta)
//   - swing_high / swing_low de las ultimas ~40 velas
//   - posicion REAL de la cuenta (lado, contratos, promedio, P&L abierto)
//   - ultimas N velas (OHLCV)
//
// SEGURIDAD: SOLO LEE y escribe a disco. No manda NINGUNA orden. Solo actua en State.Realtime.
// Escritura atomica (archivo .tmp -> copia) para que el lector no agarre un JSON a medias.
// Numeros SIEMPRE en formato invariante (punto decimal) para que el JSON sea valido.

#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using NinjaTrader.Cbi;
using NinjaTrader.Core;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    public class BridgeMercado_Snapshot : Indicator
    {
        [NinjaScriptProperty]
        [Display(Name = "Cuenta (nombre exacto)", GroupName = "1 Puente", Order = 0)]
        public string Cuenta { get; set; } = "Sim101";

        [NinjaScriptProperty]
        [Display(Name = "Ruta del archivo JSON", GroupName = "1 Puente", Order = 1)]
        public string RutaArchivo { get; set; } = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "nt8-bridge", "snapshot.json");

        [NinjaScriptProperty]
        [Range(1, 60)]
        [Display(Name = "Intervalo de escritura (seg)", GroupName = "1 Puente", Order = 2)]
        public int IntervaloSegundos { get; set; } = 2;

        [NinjaScriptProperty]
        [Range(5, 200)]
        [Display(Name = "Velas a incluir", GroupName = "1 Puente", Order = 3)]
        public int NumVelas { get; set; } = 25;

        [NinjaScriptProperty]
        [Range(1, 1000)]
        [Display(Name = "EMA rapida", GroupName = "2 EMAs", Order = 0)]
        public int PeriodoEmaRapida { get; set; } = 20;

        [NinjaScriptProperty]
        [Range(1, 1000)]
        [Display(Name = "EMA lenta", GroupName = "2 EMAs", Order = 1)]
        public int PeriodoEmaLenta { get; set; } = 200;

        private EMA emaR, emaL;
        private Account account;
        private DateTime lastWrite = DateTime.MinValue;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name        = "BridgeMercado_Snapshot";
                Description = "Escribe un snapshot JSON (precio, EMAs, velas, posicion) para el conector MCP de analisis.";
                Calculate   = Calculate.OnEachTick;
                IsOverlay   = true;
                DisplayInDataBox = false;
                IsSuspendedWhileInactive = true;
            }
            else if (State == State.Configure)
            {
                lock (Account.All)
                    account = Account.All.FirstOrDefault(a => a.Name == Cuenta);
            }
            else if (State == State.DataLoaded)
            {
                emaR = EMA(PeriodoEmaRapida);
                emaL = EMA(PeriodoEmaLenta);
            }
        }

        protected override void OnBarUpdate()
        {
            if (State != State.Realtime) return;
            if (CurrentBar < 1) return;
            if ((DateTime.Now - lastWrite).TotalSeconds < IntervaloSegundos) return;
            lastWrite = DateTime.Now;
            try { EscribirSnapshot(); }
            catch (Exception ex) { Print("BridgeMercado: " + ex.Message); }
        }

        private static string F(double v) { return v.ToString("0.########", CultureInfo.InvariantCulture); }

        private void EscribirSnapshot()
        {
            int    n     = Math.Min(NumVelas, CurrentBar + 1);
            double price = Close[0];
            bool   emaLok = CurrentBar >= PeriodoEmaLenta;
            double e20   = emaR[0];
            double e200  = emaLok ? emaL[0] : double.NaN;

            double hh = double.MinValue, ll = double.MaxValue;
            int look = Math.Min(40, CurrentBar);
            for (int i = 0; i <= look; i++) { if (High[i] > hh) hh = High[i]; if (Low[i] < ll) ll = Low[i]; }

            string lado = "FLAT"; int qty = 0; double avg = 0, pnl = 0;
            if (account != null)
            {
                try
                {
                    var pos = account.Positions.FirstOrDefault(
                        p => p.Instrument != null && p.Instrument.FullName == Instrument.FullName);
                    if (pos != null && pos.MarketPosition != MarketPosition.Flat)
                    {
                        lado = pos.MarketPosition.ToString();
                        qty  = pos.Quantity;
                        avg  = pos.AveragePrice;
                        pnl  = pos.GetUnrealizedProfitLoss(PerformanceUnit.Currency, price);
                    }
                }
                catch { }
            }

            var sb = new StringBuilder(4096);
            sb.Append("{");
            sb.Append("\"ts\":\"").Append(DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss")).Append("\",");
            sb.Append("\"instrumento\":\"").Append(Instrument.FullName).Append("\",");
            sb.Append("\"timeframe\":\"").Append(BarsPeriod.Value).Append(" ").Append(BarsPeriod.BarsPeriodType).Append("\",");
            sb.Append("\"precio\":").Append(F(price)).Append(",");
            sb.Append("\"ema_rapida_periodo\":").Append(PeriodoEmaRapida).Append(",");
            sb.Append("\"ema_lenta_periodo\":").Append(PeriodoEmaLenta).Append(",");
            sb.Append("\"ema_rapida\":").Append(F(e20)).Append(",");
            sb.Append("\"ema_lenta\":").Append(emaLok ? F(e200) : "null").Append(",");
            sb.Append("\"tendencia\":\"").Append(emaLok ? (e20 > e200 ? "alcista" : "bajista") : "indefinida").Append("\",");
            sb.Append("\"swing_high\":").Append(F(hh)).Append(",");
            sb.Append("\"swing_low\":").Append(F(ll)).Append(",");
            sb.Append("\"posicion\":{\"lado\":\"").Append(lado).Append("\",\"contratos\":").Append(qty)
              .Append(",\"promedio\":").Append(F(avg)).Append(",\"pnl_abierto\":").Append(F(pnl)).Append("},");
            sb.Append("\"velas\":[");
            for (int i = n - 1; i >= 0; i--)
            {
                sb.Append("{\"t\":\"").Append(Time[i].ToString("yyyy-MM-ddTHH:mm:ss")).Append("\",")
                  .Append("\"o\":").Append(F(Open[i])).Append(",")
                  .Append("\"h\":").Append(F(High[i])).Append(",")
                  .Append("\"l\":").Append(F(Low[i])).Append(",")
                  .Append("\"c\":").Append(F(Close[i])).Append(",")
                  .Append("\"v\":").Append(F(Volume[i])).Append("}");
                if (i > 0) sb.Append(",");
            }
            sb.Append("]}");

            string dir = Path.GetDirectoryName(RutaArchivo);
            if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir)) Directory.CreateDirectory(dir);
            string tmp = RutaArchivo + ".tmp";
            File.WriteAllText(tmp, sb.ToString(), new UTF8Encoding(false));
            File.Copy(tmp, RutaArchivo, true);
            File.Delete(tmp);
        }
    }
}
