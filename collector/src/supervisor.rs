// Supervisor: spawns and monitors one collector task per symbol.
//
// All collectors run concurrently. The supervisor logs a periodic health
// summary showing how many collectors are active. Shutdown propagates to all
// collectors via a shared watch channel.

use std::sync::Arc;

use tokio::sync::watch;
use tokio::task::JoinHandle;
use tracing::info;

use crate::collector::run_collector;
use crate::config::Config;
use crate::nats_client::NatsClient;

pub struct Supervisor {
    config: Arc<Config>,
    nats: Arc<NatsClient>,
}

impl Supervisor {
    pub fn new(config: Arc<Config>, nats: Arc<NatsClient>) -> Self {
        Supervisor { config, nats }
    }

    /// Spawn a collector task for each configured symbol.
    /// Returns the join handles so the caller can await them for clean shutdown.
    pub fn spawn_collectors(&self, shutdown: watch::Receiver<bool>) -> Vec<JoinHandle<()>> {
        self.config
            .symbols
            .iter()
            .map(|symbol| {
                let symbol = symbol.clone();
                let config = Arc::clone(&self.config);
                let nats = Arc::clone(&self.nats);
                let shutdown_rx = shutdown.clone();

                info!(symbol = %symbol, "Spawning collector task");

                tokio::spawn(async move {
                    run_collector(symbol, config, nats, shutdown_rx).await;
                })
            })
            .collect()
    }
}
