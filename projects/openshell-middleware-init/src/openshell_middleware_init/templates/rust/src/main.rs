use std::{env, error::Error, net::SocketAddr};

use __PACKAGE_NAME__::{Middleware, pb::supervisor_middleware_server::SupervisorMiddlewareServer};
use tonic::transport::Server;

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let listen = env::args()
        .nth(1)
        .unwrap_or_else(|| "127.0.0.1:50051".to_owned());
    let address: SocketAddr = listen.parse()?;

    println!("serving __SERVICE_NAME__ on {address}");
    Server::builder()
        .add_service(SupervisorMiddlewareServer::new(Middleware))
        .serve(address)
        .await?;
    Ok(())
}
