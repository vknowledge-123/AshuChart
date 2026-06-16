#!/usr/bin/env python3
"""
SSL Certificate Generator
Generates self-signed SSL certificates for HTTPS testing.

For production use, replace these with proper certificates from Let's Encrypt.
"""

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import datetime
import os


def generate_self_signed_cert(
    cert_file="ssl_cert.pem",
    key_file="ssl_key.pem",
    common_name="134.195.138.91",
    validity_days=365
):
    """
    Generate a self-signed SSL certificate.
    
    Args:
        cert_file: Output path for the certificate file
        key_file: Output path for the private key file
        common_name: Domain name or IP address (default: your server IP)
        validity_days: Certificate validity period in days
    """
    print(f"🔐 Generating self-signed SSL certificate...")
    print(f"   Common Name: {common_name}")
    print(f"   Validity: {validity_days} days")
    
    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    # Create certificate subject
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Maharashtra"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Mumbai"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AshAlgo Trading"),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])
    
    # Create certificate
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow()
    ).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(days=validity_days)
    ).add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName(common_name) if not common_name.replace('.', '').isdigit() else x509.IPAddress(common_name),
        ]),
        critical=False,
    ).sign(private_key, hashes.SHA256(), default_backend())
    
    # Write private key to file
    with open(key_file, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
    print(f"   ✅ Private key saved: {key_file}")
    
    # Write certificate to file
    with open(cert_file, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print(f"   ✅ Certificate saved: {cert_file}")
    
    print(f"\n✨ SSL certificate generated successfully!")
    print(f"\n⚠️  WARNING: This is a self-signed certificate.")
    print(f"   - Use for testing only")
    print(f"   - Browsers will show security warnings")
    print(f"   - For production, use Let's Encrypt or a trusted CA")
    print(f"\n📝 Next steps:")
    print(f"   1. Run: start_https.bat")
    print(f"   2. Access: https://{common_name}:8000")
    print(f"   3. For production setup, see SSL_SETUP_GUIDE.md")


if __name__ == "__main__":
    # Check if files already exist
    if os.path.exists("ssl_cert.pem") or os.path.exists("ssl_key.pem"):
        response = input("\n⚠️  SSL certificate files already exist. Overwrite? (y/N): ")
        if response.lower() != 'y':
            print("❌ Aborted. Using existing certificates.")
            exit(0)
    
    try:
        generate_self_signed_cert()
    except Exception as e:
        print(f"\n❌ Error generating certificate: {e}")
        print(f"\n💡 Make sure you have cryptography installed:")
        print(f"   pip install cryptography")
        exit(1)
