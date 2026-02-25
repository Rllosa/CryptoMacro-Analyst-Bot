class AlertRouter:
    def get_channels(self, alert_type: str, severity: str) -> list[str]:
        channels = ["alerts_all"]

        if severity == "HIGH":
            channels.append("alerts_high")

        if alert_type == "REGIME_SHIFT":
            channels.append("regime_shifts")
        elif alert_type in ("EXCHANGE_INFLOW_RISK", "NETFLOW_SHIFT"):
            channels.append("onchain")

        return channels
