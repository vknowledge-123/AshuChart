import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from subprocess import CompletedProcess

# Ensure app startup uses in-memory store (no Redis/Kite required)
os.environ.setdefault("APP_TESTING", "1")

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._cm = TestClient(app)
        cls.client = cls._cm.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._cm.__exit__(None, None, None)

    def test_root_redirects_to_dashboard(self) -> None:
        r = self.client.get("/", follow_redirects=False)
        self.assertIn(r.status_code, (302, 307))
        self.assertEqual(r.headers.get("location"), "/dashboard")

    def test_dashboard_renders(self) -> None:
        r = self.client.get("/dashboard")
        self.assertEqual(r.status_code, 200)
        self.assertIn("AlgoEdge", r.text)
        self.assertIn('id="webhook_url"', r.text)
        self.assertIn('id="cfg_strategy_mode"', r.text)
        self.assertIn('id="cfg_order_timeout"', r.text)
        self.assertIn('id="cfg_order_retries"', r.text)
        self.assertIn('value="GMMA_OBV"', r.text)
        self.assertIn('id="gmmaObvPanel"', r.text)
        self.assertIn('value="GMMA_GOLD_CROSS"', r.text)
        self.assertIn('id="gmmaGoldCrossPanel"', r.text)
        self.assertIn('id="bt_ggc_panel"', r.text)
        self.assertIn('id="bt_ggc_entry_mode"', r.text)
        self.assertIn('id="bt_symbol"', r.text)
        self.assertIn('id="bt_detail_body"', r.text)
        self.assertIn("Candle Detail Report", r.text)
        self.assertIn("GMMA OBV", r.text)
        self.assertIn('id="bt_timeframe"', r.text)
        self.assertIn('id="bt_liq_panel"', r.text)
        self.assertIn('id="bt_liq_gk_len"', r.text)
        self.assertIn('id="bt_liq_vol_mult"', r.text)
        self.assertIn('id="bt_liq_htf_ema"', r.text)
        self.assertIn('value="PURE_LIQUIDITY_SWEEP"', r.text)
        self.assertIn('id="pureLiquiditySweepPanel"', r.text)
        self.assertIn('id="bt_pliq_panel"', r.text)
        self.assertIn('id="bt_pliq_mode"', r.text)
        self.assertIn('value="GVK_TREND"', r.text)
        self.assertIn('id="gvkTrendPanel"', r.text)
        self.assertIn('id="bt_gvk_panel"', r.text)
        self.assertIn('id="bt_gvk_len"', r.text)
        self.assertIn('id="bt_gvk_exit_reversal"', r.text)
        self.assertIn('value="LIQUIDITY_SWEEP"', r.text)
        self.assertIn('id="liquiditySweepPanel"', r.text)
        self.assertIn("/api/backtest", r.text)
        self.assertIn('id="broker_select"', r.text)
        self.assertIn('id="dhan_access_token"', r.text)

    def test_broker_config_supports_dhan_and_zerodha(self) -> None:
        missing = self.client.post(
            "/api/broker-config",
            json={"user_id": 1, "broker": "DHAN"},
        )
        self.assertEqual(missing.json()["error"], "DHAN_CLIENT_ID_ACCESS_TOKEN_REQUIRED")

        saved = self.client.post(
            "/api/broker-config",
            json={
                "user_id": 1,
                "broker": "DHAN",
                "client_id": "test-client",
                "access_token": "test-token",
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["broker"], "DHAN")
        self.assertEqual(self.client.get("/api/broker-config").json()["broker"], "DHAN")
        self.assertEqual(self.client.get("/api/broker-status").json()["broker"], "DHAN")

        restored = self.client.post(
            "/api/broker-config",
            json={"user_id": 1, "broker": "ZERODHA"},
        )
        self.assertEqual(restored.json()["broker"], "ZERODHA")

    def test_zerodha_status_and_kill_switch(self) -> None:
        r = self.client.get("/api/zerodha-status")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["connected"], False)
        self.assertEqual(r.json()["ticker_connected"], False)
        self.assertEqual(r.json()["kill_switch"], False)

        r2 = self.client.post("/api/kill-switch", json={"user_id": 1, "enabled": True})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["ok"], True)
        self.assertEqual(r2.json()["enabled"], True)

        r3 = self.client.get("/api/zerodha-status")
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(r3.json()["kill_switch"], True)

    def test_zerodha_status_reports_valid_session_even_if_ticker_is_reconnecting(self) -> None:
        with patch.object(main_module, "is_session_valid", AsyncMock(return_value=True)):
            with patch.object(main_module, "KT_CONNECTED", False):
                with patch.object(main_module, "KT_USER_ID", None):
                    r = self.client.get("/api/zerodha-status")

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["connected"], True)
        self.assertEqual(r.json()["ticker_connected"], False)

    def test_service_restart_runs_configured_command(self) -> None:
        done = CompletedProcess(args="echo ok", returncode=0, stdout="accepted\n", stderr="")
        with patch.object(main_module, "ENABLE_SERVICE_RESTART", True):
            with patch.object(main_module, "SERVICE_RESTART_TOKEN", ""):
                with patch.object(main_module, "TRADING_RESTART_CMD", "echo ok"):
                    with patch("app.main.subprocess.run", return_value=done) as run_mock:
                        r = self.client.post("/api/service/restart", json={"user_id": 1})

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["ok"], True)
        self.assertEqual(body["message"], "Restart requested")
        self.assertIn("accepted", body["detail"])
        run_mock.assert_called_once()

    def test_service_restart_returns_error_when_command_fails(self) -> None:
        failed = CompletedProcess(args="bad cmd", returncode=1, stdout="", stderr="access denied")
        with patch.object(main_module, "ENABLE_SERVICE_RESTART", True):
            with patch.object(main_module, "SERVICE_RESTART_TOKEN", ""):
                with patch.object(main_module, "TRADING_RESTART_CMD", "bad cmd"):
                    with patch("app.main.subprocess.run", return_value=failed):
                        r = self.client.post("/api/service/restart", json={"user_id": 1})

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["ok"], False)
        self.assertEqual(body["error"], "RESTART_CMD_FAILED:1")
        self.assertIn("access denied", body["detail"])

    def test_auto_squareoff_toggle(self) -> None:
        r = self.client.get("/api/auto-sq-off/status", params={"user_id": 1})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["enabled"], False)

        r2 = self.client.post("/api/auto-sq-off/toggle", json={"user_id": 1, "enabled": True})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["enabled"], True)

        r3 = self.client.get("/api/auto-sq-off/status", params={"user_id": 1})
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(r3.json()["enabled"], True)

    def test_subscribe_symbols_validation(self) -> None:
        r = self.client.post("/api/subscribe-symbols", json={"user_id": 1, "symbols": []})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["ok"], False)
        self.assertEqual(r.json()["error"], "NO_SYMBOLS")

        r2 = self.client.post("/api/subscribe-symbols", json={"user_id": 1, "symbols": ["SBIN"]})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["ok"], True)
        self.assertEqual(r2.json()["count"], 1)

    def test_ws_feed_http_returns_upgrade_required(self) -> None:
        r = self.client.get("/ws/feed", params={"user_id": 1})
        self.assertEqual(r.status_code, 426)
        body = r.json()
        self.assertIn("detail", body)

    def test_precision_sniper_config_is_saved(self) -> None:
        payload = {
            "user_id": 1,
            "alert_name": "SNIPER_TEST",
            "enabled": True,
            "direction": "BOTH",
            "product": "MIS",
            "strategy_mode": "PRECISION_SNIPER",
            "custom_preset": "Custom",
            "custom_ema_fast": 9,
            "custom_ema_slow": 21,
            "custom_ema_trend": 55,
            "custom_tp1_mult": 1,
            "custom_tp2_mult": 2,
            "custom_tp3_mult": 3,
            "custom_htf_minutes": 5,
        }
        r = self.client.post("/api/alert-config", json=payload)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "saved")
        self.assertEqual(r.json()["config"]["strategy_mode"], "PRECISION_SNIPER")

        listed = self.client.get("/api/alert-config", params={"user_id": 1}).json()["configs"]
        self.assertIn("sniper test", listed)

    def test_precision_sniper_rejects_invalid_targets(self) -> None:
        r = self.client.post(
            "/api/alert-config",
            json={
                "user_id": 1,
                "alert_name": "BAD_SNIPER",
                "strategy_mode": "PRECISION_SNIPER",
                "custom_preset": "Custom",
                "custom_ema_fast": 9,
                "custom_ema_slow": 21,
                "custom_ema_trend": 55,
                "custom_tp1_mult": 2,
                "custom_tp2_mult": 1,
                "custom_tp3_mult": 3,
                "custom_htf_minutes": 5,
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["error"], "CUSTOM_TP_MULTIPLIERS_INVALID")

    def test_gmma_obv_config_is_saved(self) -> None:
        payload = {
            "user_id": 1,
            "alert_name": "GMMA_TEST",
            "enabled": True,
            "direction": "BOTH",
            "product": "MIS",
            "strategy_mode": "GMMA_OBV",
            "order_confirm_timeout_sec": 1.5,
            "order_pending_retry_count": 1,
            "gmma_adx_min": 21,
            "gmma_adx_len": 14,
            "gmma_atr_len": 14,
            "gmma_sl_mult": 1.2,
            "gmma_tp1_mult": 1,
            "gmma_tp2_mult": 2,
            "gmma_tp3_mult": 3,
            "gmma_obv_fast": 5,
            "gmma_obv_medium": 9,
            "gmma_obv_slow": 14,
            "gmma_obv_donchian": 26,
        }
        r = self.client.post("/api/alert-config", json=payload)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "saved")
        self.assertEqual(r.json()["config"]["strategy_mode"], "GMMA_OBV")
        self.assertEqual(r.json()["config"]["order_pending_retry_count"], 1)

    def test_gmma_obv_rejects_invalid_targets(self) -> None:
        r = self.client.post(
            "/api/alert-config",
            json={
                "user_id": 1,
                "alert_name": "BAD_GMMA",
                "strategy_mode": "GMMA_OBV",
                "gmma_tp1_mult": 2,
                "gmma_tp2_mult": 1,
                "gmma_tp3_mult": 3,
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["error"], "GMMA_TP_MULTIPLIERS_INVALID")

    def test_gmma_gold_cross_config_and_backtest_settings(self) -> None:
        payload = {
            "user_id": 1,
            "alert_name": "GMMA_GC_TEST",
            "enabled": True,
            "direction": "BOTH",
            "product": "MIS",
            "strategy_mode": "GMMA_GOLD_CROSS",
            "strategy_timeframe_minutes": 5,
            "ggc_entry_mode": "Regime Mode",
            "ggc_require_obv": False,
            "ggc_allow_shorts": True,
            "ggc_obv_fast": 5,
            "ggc_obv_medium": 9,
            "ggc_obv_slow": 14,
            "ggc_obv_donchian": 26,
            "ggc_atr_len": 14,
            "ggc_sl_mult": 1.2,
            "ggc_tp1_mult": 1,
            "ggc_tp2_mult": 2,
            "ggc_tp3_mult": 3,
        }
        saved = self.client.post("/api/alert-config", json=payload)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["status"], "saved")
        self.assertEqual(saved.json()["config"]["strategy_mode"], "GMMA_GOLD_CROSS")

        start = datetime(2026, 6, 12, 9, 15)
        candles = []
        for i in range(110):
            close = 100 + i * 0.4
            candles.append(
                {
                    "date": (start + timedelta(minutes=5 * i)).isoformat(),
                    "open": close - 0.1,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 1000 + i,
                }
            )
        r = self.client.post(
            "/api/backtest",
            json={
                "user_id": 1,
                "symbol": "SBIN",
                "strategy_mode": "GMMA_GOLD_CROSS",
                "strategy_timeframe_minutes": 5,
                "from_date": candles[80]["date"],
                "to_date": candles[-1]["date"],
                "qty": 1,
                "ggc_entry_mode": "Regime Mode",
                "ggc_require_obv": False,
                "candles": candles,
            },
        )

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        params = body["result"]["parameters"]
        self.assertEqual(params["preset"], "GMMA_GOLD_CROSS_5M")
        self.assertEqual(params["entry_mode"], "Regime Mode")
        self.assertEqual(params["require_obv"], False)
        self.assertTrue(any("ggc_gc_regime" in row.get("indicators", {}) for row in body["result"]["detail_report"]))

    def test_backtest_api_runs_with_supplied_warmup_candles(self) -> None:
        start = datetime(2026, 6, 12, 9, 15)
        candles = []
        for i in range(140):
            close = 100 + i * 0.35
            candles.append(
                {
                    "date": (start + timedelta(minutes=5 * i)).isoformat(),
                    "open": close - 0.1,
                    "high": close + 0.7,
                    "low": close - 0.3,
                    "close": close,
                    "volume": 1500 + i * 10,
                }
            )
        r = self.client.post(
            "/api/backtest",
            json={
                "user_id": 1,
                "symbol": "SBIN",
                "strategy_mode": "GMMA_OBV",
                "from_date": candles[80]["date"],
                "to_date": candles[-1]["date"],
                "qty": 10,
                "gmma_require_gc": False,
                "gmma_adx_min": 10,
                "gmma_tp1_mult": 0.5,
                "gmma_tp2_mult": 1,
                "gmma_tp3_mult": 1.5,
                "candles": candles,
            },
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertGreater(body["result"]["warmup_candles"], 0)
        self.assertGreater(body["result"]["total_trades"], 0)
        self.assertIn("parameters", body["result"])
        self.assertGreater(len(body["result"]["detail_report"]), 0)
        self.assertTrue(
            any(row.get("indicators", {}).get("gmma_s1_ema3") is not None for row in body["result"]["detail_report"])
        )

    def test_backtest_api_extends_broker_warmup_when_first_fetch_is_short(self) -> None:
        def make_candles(warmup_count: int, in_range_count: int):
            from_dt = main_module.pytz.timezone("Asia/Kolkata").localize(datetime(2026, 6, 19, 9, 15))
            rows = []
            start = from_dt - timedelta(minutes=15 * warmup_count)
            for i in range(warmup_count + in_range_count):
                stamp = start + timedelta(minutes=15 * i)
                close = 24000 + i * 0.1
                rows.append(
                    {
                        "date": stamp.isoformat(),
                        "open": close - 0.2,
                        "high": close + 0.5,
                        "low": close - 0.5,
                        "close": close,
                        "volume": 100000 + i,
                    }
                )
            return rows

        fake_engine = type("FakeEngine", (), {})()
        fake_engine._fetch_backtest_candles = AsyncMock(
            side_effect=[
                make_candles(72, 24),
                make_candles(210, 24),
            ]
        )

        with patch.object(main_module, "ensure_engine", new=AsyncMock(return_value=fake_engine)):
            r = self.client.post(
                "/api/backtest",
                json={
                    "user_id": 1,
                    "symbol": "NIFTY",
                    "strategy_mode": "LIQUIDITY_SWEEP",
                    "strategy_timeframe_minutes": 15,
                    "from_date": "2026-06-19",
                    "to_date": "2026-06-19",
                    "qty": 1,
                },
            )

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(fake_engine._fetch_backtest_candles.await_count, 2)
        self.assertGreaterEqual(body["result"]["warmup_candles"], body["result"]["required_candles"])
        self.assertGreater(body["result"]["warmup_days_requested"], 19)

    def test_backtest_api_uses_custom_liquidity_sweep_settings(self) -> None:
        start = datetime(2026, 6, 16, 9, 15)
        candles = []
        for i in range(140):
            close = 24000 + i * 0.2
            candles.append(
                {
                    "date": (start + timedelta(minutes=5 * i)).isoformat(),
                    "open": close - 0.2,
                    "high": close + 0.8,
                    "low": close - 0.8,
                    "close": close,
                    "volume": 100000 + i * 100,
                }
            )

        r = self.client.post(
            "/api/backtest",
            json={
                "user_id": 1,
                "symbol": "NIFTY",
                "strategy_mode": "LIQUIDITY_SWEEP",
                "strategy_timeframe_minutes": 5,
                "from_date": candles[110]["date"],
                "to_date": candles[-1]["date"],
                "qty": 1,
                "liq_gk_len": 100,
                "liq_gk_mult": 1.8,
                "liq_gk_atr_len": 14,
                "liq_gk_confirm_bars": 1,
                "liq_lookback_bars": 50,
                "liq_swing_len": 13,
                "liq_minor_len": 5,
                "liq_confirm_window": 8,
                "liq_require_choch": True,
                "liq_use_volume": True,
                "liq_vol_len": 14,
                "liq_vol_mult": 1.2,
                "liq_use_gk_filter": True,
                "liq_use_htf_bias": True,
                "liq_use_sr_filter": False,
                "liq_htf_ema_len": 34,
                "liq_min_score": 40,
                "candles": candles,
            },
        )

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        params = body["result"]["parameters"]
        self.assertEqual(params["gk_len"], 100)
        self.assertEqual(params["gk_mult"], 1.8)
        self.assertEqual(params["gk_atr_len"], 14)
        self.assertEqual(params["gk_confirm_bars"], 1)
        self.assertEqual(params["lookback_bars"], 50)
        self.assertEqual(params["swing_len"], 13)
        self.assertEqual(params["minor_len"], 5)
        self.assertEqual(params["confirm_window"], 8)
        self.assertEqual(params["vol_len"], 14)
        self.assertEqual(params["vol_mult"], 1.2)
        self.assertEqual(params["use_gk_filter"], True)
        self.assertEqual(params["use_htf_bias"], True)
        self.assertEqual(params["use_sr_filter"], False)
        self.assertEqual(params["htf_ema_len"], 34)
        self.assertEqual(params["min_score"], 40)

    def test_backtest_api_uses_pure_liquidity_sweep_settings(self) -> None:
        start = datetime(2026, 6, 16, 9, 15)
        candles = []
        for i in range(80):
            low = 23990.0
            high = 24010.0
            close = 24000.0
            open_ = 24000.0
            if i == 35:
                low = 23920.0
            if i == 70:
                open_ = 23950.0
                low = 23910.0
                high = 23990.0
                close = 23960.0
            candles.append(
                {
                    "date": (start + timedelta(minutes=5 * i)).isoformat(),
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": 100000 + i * 100,
                }
            )

        r = self.client.post(
            "/api/backtest",
            json={
                "user_id": 1,
                "symbol": "NIFTY",
                "strategy_mode": "PURE_LIQUIDITY_SWEEP",
                "strategy_timeframe_minutes": 5,
                "from_date": candles[60]["date"],
                "to_date": candles[-1]["date"],
                "qty": 1,
                "pliq_swing_len": 3,
                "pliq_lookback_bars": 80,
                "pliq_sweep_mode": "Only Wicks",
                "pliq_min_score": 1,
                "pliq_require_choch": False,
                "pliq_minor_len": 2,
                "pliq_confirm_window": 8,
                "pliq_use_volume": False,
                "pliq_vol_len": 14,
                "pliq_vol_mult": 1.2,
                "pliq_use_htf_bias": False,
                "pliq_htf_ema_len": 34,
                "pliq_atr_len": 14,
                "candles": candles,
            },
        )

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        params = body["result"]["parameters"]
        self.assertEqual(params["sweep_mode"], "Only Wicks")
        self.assertEqual(params["swing_len"], 3)
        self.assertEqual(params["minor_len"], 2)
        self.assertEqual(params["use_volume"], False)
        self.assertEqual(params["use_htf_bias"], False)
        self.assertEqual(params["htf_ema_len"], 34)

    def test_gvk_trend_config_and_backtest_settings(self) -> None:
        payload = {
            "user_id": 1,
            "alert_name": "GVK_TEST",
            "enabled": True,
            "direction": "BOTH",
            "product": "MIS",
            "strategy_mode": "GVK_TREND",
            "strategy_timeframe_minutes": 15,
            "gvk_gk_len": 100,
            "gvk_gk_mult": 1.8,
            "gvk_gk_atr_len": 14,
            "gvk_gk_confirm_bars": 1,
            "gvk_entry_mode": "Trend Mode",
            "gvk_min_score": 4,
            "gvk_atr_len": 14,
            "gvk_sl_mult": 1.2,
            "gvk_tp1_mult": 1,
            "gvk_tp2_mult": 2,
            "gvk_tp3_mult": 3,
        }
        saved = self.client.post("/api/alert-config", json=payload)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["status"], "saved")
        self.assertEqual(saved.json()["config"]["strategy_mode"], "GVK_TREND")

        start = datetime(2026, 6, 16, 9, 15)
        candles = []
        for i in range(80):
            close = 100 + i * 0.2
            candles.append(
                {
                    "date": (start + timedelta(minutes=5 * i)).isoformat(),
                    "open": close - 0.1,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 1000,
                }
            )

        r = self.client.post(
            "/api/backtest",
            json={
                "user_id": 1,
                "symbol": "SBIN",
                "strategy_mode": "GVK_TREND",
                "strategy_timeframe_minutes": 5,
                "from_date": candles[40]["date"],
                "to_date": candles[-1]["date"],
                "qty": 1,
                "gvk_gk_len": 20,
                "gvk_gk_mult": 0.5,
                "gvk_gk_atr_len": 5,
                "gvk_gk_confirm_bars": 1,
                "gvk_entry_mode": "Trend Mode",
                "gvk_min_score": 4,
                "gvk_atr_len": 5,
                "candles": candles,
            },
        )

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        params = body["result"]["parameters"]
        self.assertEqual(params["gk_len"], 20)
        self.assertEqual(params["entry_mode"], "Trend Mode")
        self.assertGreater(len(body["result"]["detail_report"]), 0)
        self.assertTrue(any("gvk_zl" in row.get("indicators", {}) for row in body["result"]["detail_report"]))

    def test_backtest_api_reports_no_candles(self) -> None:
        r = self.client.post(
            "/api/backtest",
            json={
                "user_id": 1,
                "symbol": "DALBHARAT",
                "strategy_mode": "GMMA_OBV",
                "from_date": "2026-06-12",
                "to_date": "2026-06-19",
                "candles": [],
            },
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["error"], "BACKTEST_NO_CANDLES_RETURNED")
        self.assertEqual(body["symbol"], "DALBHARAT")

    def test_precision_sniper_rejects_invalid_partial_profit_total(self) -> None:
        r = self.client.post(
            "/api/alert-config",
            json={
                "user_id": 1,
                "alert_name": "BAD_PARTIAL",
                "strategy_mode": "PRECISION_SNIPER",
                "custom_partial_profit_enabled": True,
                "custom_partial_tp1_pct": 50,
                "custom_partial_tp2_pct": 25,
                "custom_partial_tp3_pct": 10,
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["error"], "CUSTOM_PARTIAL_PERCENT_TOTAL_MUST_BE_100")

    def test_chartink_webhook_upserts_one_alert_record(self) -> None:
        engine = AsyncMock()
        engine.on_chartink_alert.return_value = [
            {"symbol": "SBIN", "status": "SKIPPED", "reason": "CUSTOM_ENTRY_CHECK_FAILED"},
            {"symbol": "TCS", "status": "ENTERED", "reason": "ORDER_OK"},
        ]
        self.client.post("/api/kill-switch", json={"user_id": 1, "enabled": False})

        with patch.object(main_module, "ensure_engine", AsyncMock(return_value=engine)):
            response = self.client.post(
                "/webhook/chartink",
                params={"user_id": 1},
                json={"scan_name": "WEBHOOK_TEST", "stocks": "NSE:SBIN-EQ,TCS"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["symbols"], ["SBIN", "TCS"])
        alerts = self.client.get("/api/alerts", params={"user_id": 1, "limit": 100}).json()["alerts"]
        matching = [a for a in alerts if a.get("alert_name") == "webhook test"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(len(matching[0]["result"]), 2)


if __name__ == "__main__":
    unittest.main()
