use std::error::Error;

fn main() -> Result<(), Box<dyn Error>> {
    // Bundle protoc so contributors do not need a separate installation.
    unsafe {
        std::env::set_var("PROTOC", protobuf_src::protoc());
    }

    println!("cargo:rerun-if-changed=proto/supervisor_middleware.proto");
    tonic_prost_build::configure()
        .build_client(true)
        .build_server(true)
        .compile_protos(&["proto/supervisor_middleware.proto"], &["proto"])?;

    Ok(())
}
