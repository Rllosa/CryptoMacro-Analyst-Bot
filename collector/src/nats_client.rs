// NATS JetStream client wrapper.
//
// Wraps async-nats to provide a simple publish interface and ensures the
// MARKET stream exists before any collector starts publishing.

use anyhow::Context;
use async_nats::jetstream;
use async_nats::jetstream::stream;
use tracing::{info, warn};

pub struct NatsClient {
    jetstream: jetstream::Context,
}

impl NatsClient {
    /// Connect to NATS and ensure the MARKET JetStream stream is ready.
    /// Retries up to `max_retries` times with 2-second delays to handle
    /// NATS starting after this service in docker-compose.
    pub async fn connect(url: &str) -> anyhow::Result<Self> {
        let client = async_nats::connect(url)
            .await
            .with_context(|| format!("Failed to connect to NATS at {url}"))?;

        info!(nats_url = url, "Connected to NATS");

        let jetstream = jetstream::new(client);
        Self::ensure_stream(&jetstream).await?;

        Ok(NatsClient { jetstream })
    }

    /// Ensure the MARKET stream exists with 7-day retention.
    /// get_or_create_stream is idempotent — safe to call on every startup.
    async fn ensure_stream(js: &jetstream::Context) -> anyhow::Result<()> {
        let config = stream::Config {
            name: "MARKET".to_string(),
            subjects: vec!["market.candles.>".to_string()],
            // Retain 7 days of candle history — long enough to backfill features
            max_age: std::time::Duration::from_secs(7 * 24 * 3600),
            ..Default::default()
        };

        js.get_or_create_stream(config)
            .await
            .map_err(|e| anyhow::anyhow!("Failed to ensure NATS MARKET stream: {}", e))?;

        info!("NATS JetStream stream 'MARKET' ready (subjects: market.candles.>)");
        Ok(())
    }

    /// Publish a raw JSON payload to the given NATS subject.
    /// Awaits the publish acknowledgement for at-least-once delivery guarantee.
    pub async fn publish(&self, subject: &str, payload: Vec<u8>) -> anyhow::Result<()> {
        let ack_future = self
            .jetstream
            .publish(subject.to_string(), payload.into())
            .await
            .map_err(|e| anyhow::anyhow!("NATS publish to '{}' failed: {}", subject, e))?;

        // Wait for JetStream ack — confirms the message is durably stored
        ack_future
            .await
            .map_err(|e| anyhow::anyhow!("NATS ack for '{}' failed: {}", subject, e))?;

        Ok(())
    }

    /// Attempt NATS connection with retries.
    /// Used at startup when NATS may not yet be healthy per docker-compose healthcheck.
    pub async fn connect_with_retry(url: &str, max_retries: u32) -> anyhow::Result<Self> {
        let mut last_err = anyhow::anyhow!("No attempts made");

        for attempt in 1..=max_retries {
            match Self::connect(url).await {
                Ok(client) => return Ok(client),
                Err(e) => {
                    last_err = e;
                    if attempt < max_retries {
                        warn!(
                            attempt = attempt,
                            max_retries = max_retries,
                            error = %last_err,
                            "NATS connection failed, retrying in 2s"
                        );
                        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
                    }
                }
            }
        }

        Err(last_err)
            .with_context(|| format!("Failed to connect to NATS after {max_retries} attempts"))
    }
}
