// CryptoMacro Analyst Bot — Binance WebSocket Collector (Rust)
// DI-1: Real-time kline collector for BTC, ETH, SOL, HYPE
//
// Connects to Binance Futures WebSocket kline streams, normalizes candles to
// the unified CandleMessage schema, and publishes to NATS JetStream.
// Handles reconnection, heartbeat monitoring, and graceful SIGTERM shutdown.

use std::sync::Arc;

use tokio::signal;
use tokio::sync::watch;
use tracing::info;

use cryptomacro_collector::config::Config;
use cryptomacro_collector::nats_client::NatsClient;
use cryptomacro_collector::supervisor::Supervisor;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Load .env file — silently ignored if absent (production uses real env vars)
    dotenv::dotenv().ok();

    // Structured JSON logging to stdout, filtered by RUST_LOG env var (default: info)
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(
            tracing_subscriber::EnvFilter::from_default_env()
                .add_directive(tracing::Level::INFO.into()),
        )
        .with_current_span(false)
        .init();

    let config = Arc::new(Config::from_env());

    info!(
        service = "collector",
        symbols = ?config.symbols,
        nats_url = %config.nats_url,
        "CryptoMacro Collector starting"
    );

    // Connect to NATS with retries — collector must not crash if NATS starts slowly
    let nats = Arc::new(
        NatsClient::connect_with_retry(&config.nats_url, 10)
            .await
            .map_err(|e| {
                tracing::error!(error = %e, "Failed to connect to NATS, aborting");
                e
            })?,
    );

    // Shutdown channel: broadcast `true` to all collector tasks to stop them
    let (shutdown_tx, shutdown_rx) = watch::channel(false);

    let supervisor = Supervisor::new(Arc::clone(&config), Arc::clone(&nats));
    let handles = supervisor.spawn_collectors(shutdown_rx);

    info!(
        num_collectors = handles.len(),
        "All collector tasks spawned, waiting for shutdown signal"
    );

    // Block until SIGTERM (Docker stop) or SIGINT (Ctrl+C)
    wait_for_shutdown_signal().await;

    info!("Shutdown signal received, stopping all collectors...");

    // Signal all collector tasks to stop
    if shutdown_tx.send(true).is_err() {
        // All receivers dropped — collectors already exited
        tracing::warn!("Shutdown channel closed before signal sent");
    }

    // Wait for all collectors to finish (with a reasonable timeout)
    for handle in handles {
        if let Err(e) = handle.await {
            tracing::error!(error = ?e, "Collector task panicked during shutdown");
        }
    }

    info!("All collectors stopped. Goodbye.");
    Ok(())
}

/// Wait for SIGTERM (docker stop) or SIGINT (Ctrl+C).
async fn wait_for_shutdown_signal() {
    let ctrl_c = async {
        signal::ctrl_c()
            .await
            .expect("Failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let sigterm = async {
        signal::unix::signal(signal::unix::SignalKind::terminate())
            .expect("Failed to install SIGTERM handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let sigterm = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => { info!("Received SIGINT (Ctrl+C)"); }
        _ = sigterm => { info!("Received SIGTERM"); }
    }
}
