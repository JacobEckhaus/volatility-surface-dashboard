import threading
import time
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from matplotlib.widgets import Button
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from matplotlib.ticker import PercentFormatter

plt.style.use('dark_background')

class LiveSurfaceApp(EClient, EWrapper):

    def __init__(self):
        EClient.__init__(self, self)
        self.iv_dict = {}
        self.id_map = {}
        self.expirations = {}
        self.strikes = {}
        self.spot_price = 0
        self.underlying_conId = 0
        self.resolved = threading.Event()
        self.chain_resolved = threading.Event()

    def connectAck(self):
        print("TWS Acknowledged the Connection")

    def error(
        self,
        reqId,
        errorTime,
        errorCode,
        errorString,
        advancedOrderRejectJson=""
    ):
        if errorCode not in [2104, 2106, 2158]:
            print(
                f"[{errorTime}] Error {errorCode} "
                f"(reqId={reqId}): {errorString}"
            )

    def contractDetails(self, reqId, contractDetails):
        self.underlying_conId = contractDetails.contract.conId
        self.resolved.set()

    def tickPrice(self, reqId, tickType, price, attrib):
        if reqId == 999 and tickType in [4, 9, 68] and price > 0:
            self.spot_price = price

    def securityDefinitionOptionParameter(
        self,
        reqId,
        exchange,
        underlyingConId,
        tradingClass,
        multiplier,
        expirations,
        strikes,
    ):
        if (
            exchange == "SMART"
            and tradingClass == "SPY"
            and multiplier == "100"
        ):
            self.expirations = sorted(expirations)
            self.strikes = sorted(strikes)
            print(
                f"Selected SPY chain: "
                f"{len(self.expirations)} expirations, "
                f"{len(self.strikes)} strikes"
            )
            self.chain_resolved.set()
    
    

    
    
    def tickOptionComputation(
        self,
        reqId,
        tickType,
        tickAttrib,
        impliedVol,
        delta,
        optPrice,
        pvDividend,
        gamma,
        vega,
        theta,
        undPrice,
    ):
        if (
            tickType in (13, 83)
            and impliedVol is not None
            and np.isfinite(impliedVol)
            and 0 < impliedVol < 10
        ):
            self.iv_dict[reqId] = impliedVol


def run_loop(app):
    app.run()


def start_app(symbol='SPY'):
    app = LiveSurfaceApp()
    app.connect('127.0.0.1', 7497, 35)

    api_thread = threading.Thread(
        target=run_loop,
        args=(app,),
        daemon=True,
    )
    api_thread.start()

    time.sleep(2)

    app.reqMarketDataType(3)

    print("Connected:", app.isConnected())

    underlying = Contract()
    underlying.symbol = symbol
    underlying.secType = 'STK'
    underlying.exchange = 'SMART'
    underlying.currency = 'USD'

    app.reqContractDetails(1, underlying)
    app.resolved.wait(timeout=5)

    app.reqMktData(999, underlying, "", False, False, [])
    start = time.time()

    while app.spot_price == 0:
        if time.time() - start > 10:
            raise TimeoutError("No market data received.")
        time.sleep(.1)

    spot = app.spot_price

    app.reqSecDefOptParams(2, symbol, "", "STK", app.underlying_conId)
     
    if not app.chain_resolved.wait(timeout=15):
        app.disconnect()
        raise TimeoutError("Regular SPY option chain was not received.")
    

    today = time.strftime('%Y%m%d')
    target_exps = [e for e in app.expirations if e >= today][:6]
    target_strikes = [s for s in app.strikes if spot * 0.98 <= s <= spot * 1.02]
                 
    print("SPY price:", spot)
    print("Selected expirations:", target_exps)
    print("Selected strikes:", target_strikes)
    print("Option requests planned:", len(target_exps) * len(target_strikes))
    req_id = 1000
    for exp in target_exps:
        for strike in target_strikes:
            opt = Contract()
            opt.symbol = symbol
            opt.secType = 'OPT'
            opt.exchange = 'SMART'
            opt.currency = 'USD'
            opt.multiplier = "100"
            opt.lastTradeDateOrContractMonth = exp
            opt.strike = strike
            opt.right = 'C' if strike >= spot else 'P'
            app.id_map[req_id] = (exp, strike)

            app.reqMktData(req_id, opt, "", False, False, [])
            req_id += 1
            time.sleep(.1)

    return app


class PlotState:

    def __init__(self):
        self.is_locked = False

    def toggle(self, event):
        self.is_locked = not self.is_locked
        btn_label.set_text(
            "UNLOCK UPDATES" if self.is_locked else "LOCK UPDATES"
        )
        plt.draw()


def live_desktop_plot(app):
    plt.ion()
    fig = plt.figure(figsize=(16, 9))
    fig.canvas.manager.set_window_title("Live Volatility Surface")
    fig.patch.set_facecolor('#0b0d0f')

    ax_3d = plt.subplot2grid((1, 3), (0, 0), colspan=2, projection='3d')
    ax_skew = plt.subplot2grid((1, 3), (0, 2))

    state = PlotState()
    ax_button = plt.axes([.42, .03, .12, .04])
    global btn_label
    btn = Button(ax_button, 'LOCK UPDATES', color='#1f2329', hovercolor='#2d333b')
    btn_label = btn.label
    btn_label.set_color('white')
    btn_label.set_fontsize(9)
    btn.on_clicked(state.toggle)

    print(" --- Live Implied Volatility Surface Started")

    try:
        while True:
            if not state.is_locked:
                current_data = []
                req_ids = list(app.iv_dict.keys())
                for rid in req_ids:
                    iv = app.iv_dict[rid]
                    exp, strike = app.id_map[rid]
                    current_data.append({'Expiry': exp, 'Strike': strike, 'IV': iv})

                if len(current_data) > 10:
                    df = pd.DataFrame(current_data)
                    pivot = df.pivot_table(index='Expiry', columns='Strike', values='IV').sort_index().sort_index(axis=1)
                    pivot = pivot.interpolate(method='linear', axis=0).bfill().ffill()

                    X, Y_idx = np.meshgrid(pivot.columns, np.arange(len(pivot.index)))
                    Z = pivot.values

                    curr_elev, curr_azim = ax_3d.elev, ax_3d.azim

                    ax_3d.clear()
                    ax_3d.set_facecolor('#0b0d0f')
                    ax_3d.plot_surface(X, Y_idx, Z, cmap='magma', edgecolor='white', lw=.1, alpha=.9)

                    ax_3d.set_yticks(np.arange(len(pivot.index)))
                    ax_3d.set_yticklabels(pivot.index)


                    ax_3d.set_title(
                        f"SPY Implied Volatility Surface | Delayed Data\n"
                        f"Updated {time.strftime('%H:%M:%S')}",
                        color="white",
                    )
                    ax_3d.set_xlabel("Strike ($)", labelpad=10)
                    ax_3d.set_ylabel("Expiration", labelpad=12)
                    ax_3d.set_zlabel("Implied Volatility", labelpad=10)
                    ax_3d.zaxis.set_major_formatter(
                        PercentFormatter(xmax=1)
                    )


                    ax_3d.view_init(elev=curr_elev, azim=curr_azim)


                    ax_skew.clear()
                    ax_skew.set_facecolor("#161b22")
                    nearest_exp = pivot.index[0]
                    skew_data = pivot.iloc[0]

                    ax_skew.set_title(
                        f"Nearest-Expiration Skew\n{nearest_exp} | Delayed Data",
                        color="white",
                    )
                    ax_skew.set_xlabel("Strike ($)")
                    ax_skew.set_ylabel("Implied Volatility")
                    ax_skew.yaxis.set_major_formatter(
                        PercentFormatter(xmax=1)
                    )
                    ax_skew.axvline(
                        x=app.spot_price,
                        color="#ff3e3e",
                        linestyle="--",
                        label=f"SPY spot: ${app.spot_price:.2f}",
                    )
                    ax_skew.plot(
                        skew_data.index,
                        skew_data.values,
                        marker="o",
                        color="#00f2ff",
                    )
                    ax_skew.legend(loc="upper right", fontsize=9)



            plt.pause(.5)

    except KeyboardInterrupt:
        app.disconnect()
        plt.close()


if __name__ == '__main__':
    app_instance = start_app()
    print("App Started")
    time.sleep(10)
    live_desktop_plot(app_instance)








              