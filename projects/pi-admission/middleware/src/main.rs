use std::{env, error::Error, fs, net::SocketAddr, path::PathBuf, sync::Arc};

use ed25519_dalek::SigningKey;
use pi_admission::{AdmissionConfig, Middleware, ReceiptAuthority, admission_router};
use rand::rngs::OsRng;
use tonic::transport::{Identity, Server, ServerTlsConfig};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let path = config_path()?;
    let config: AdmissionConfig = serde_json::from_slice(&fs::read(path)?)?;
    let config = Arc::new(config);
    let receipts = Arc::new(ReceiptAuthority::new(SigningKey::generate(&mut OsRng)));
    let middleware = Middleware::from_config(config.clone(), receipts.clone())?;
    let certificate = fs::read(&config.tls_certificate)?;
    let private_key = fs::read(&config.tls_private_key)?;
    let grpc_address: SocketAddr = "0.0.0.0:50051".parse()?;
    let admission_address: SocketAddr = config.listen.parse()?;
    let tls =
        axum_server::tls_rustls::RustlsConfig::from_pem(certificate.clone(), private_key.clone())
            .await?;

    println!("serving Pi admission HTTPS on {admission_address} and gRPC on {grpc_address}");
    let grpc = Server::builder()
        .tls_config(ServerTlsConfig::new().identity(Identity::from_pem(certificate, private_key)))?
        .add_service(middleware.service())
        .serve(grpc_address);
    let http = axum_server::bind_rustls(admission_address, tls)
        .serve(admission_router(config, receipts).into_make_service());
    tokio::try_join!(
        async {
            grpc.await
                .map_err(|error| -> Box<dyn Error> { Box::new(error) })
        },
        async {
            http.await
                .map_err(|error| -> Box<dyn Error> { Box::new(error) })
        },
    )?;
    Ok(())
}

fn config_path() -> Result<PathBuf, Box<dyn Error>> {
    let mut arguments = env::args_os().skip(1);
    if arguments.next().as_deref() != Some("--config".as_ref()) {
        return Err("usage: pi-admission --config PATH".into());
    }
    let path = arguments.next().ok_or("missing configuration path")?;
    if arguments.next().is_some() {
        return Err("unexpected argument".into());
    }
    Ok(path.into())
}
