from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import datetime
import http.server, ssl

if __name__ == '__main__':
    # Generate the issuer's RSA public-private key pair
    issuer_rsa = rsa.generate_private_key(
         public_exponent=65537,
         key_size=2048,
    )

    # Generate the issuer's certificate
    issuer_cert_name = x509.Name(
        [x509.NameAttribute(NameOID.COUNTRY_NAME, "CZ"),
         x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Prague"),
         x509.NameAttribute(NameOID.LOCALITY_NAME, "Prague"),
         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DesignPatternsInTheWild"),
         x509.NameAttribute(NameOID.COMMON_NAME, "DesignPatternsInTheWildCA")]
    )

    issuer_cert_duration = 3650 * datetime.timedelta(1, 0, 0)
    issuer_certificate = (
        x509.CertificateBuilder()
            .subject_name(issuer_cert_name)
            .issuer_name(issuer_cert_name)
            .not_valid_before(datetime.datetime.today() - datetime.timedelta(1, 0, 0))
            .not_valid_after(datetime.datetime.today() + issuer_cert_duration)
            .public_key(issuer_rsa.public_key())
            .serial_number(x509.random_serial_number())
            .add_extension(
            x509.BasicConstraints(
                ca=True,
                path_length=None
            ),
            critical=True,
        )
            .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=False,
        )
            .sign(private_key=issuer_rsa, algorithm=hashes.SHA256())
    )

    # Generate the server's RSA public-private key pair
    server_rsa = rsa.generate_private_key(
         public_exponent=65537,
         key_size=2048,
    )

    # Generate the server's certificate
    server_cert_name = x509.Name(
        [x509.NameAttribute(NameOID.COUNTRY_NAME, "CZ"),
         x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Prague"),
         x509.NameAttribute(NameOID.LOCALITY_NAME, "Prague"),
         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "DesignPatternsInTheWild"),
         x509.NameAttribute(NameOID.COMMON_NAME, "DesignPatternsInTheWildServer")]
    )

    server_certificate_duration = 30 * datetime.timedelta(1, 0, 0)
    server_certificate = (
        x509.CertificateBuilder()
            .subject_name(x509.Name(server_cert_name))
            .issuer_name(issuer_certificate.subject)
            .not_valid_before(datetime.datetime.today() - datetime.timedelta(1, 0, 0))
            .not_valid_after(datetime.datetime.today() + server_certificate_duration)
            .public_key(server_rsa.public_key())
            .serial_number(x509.random_serial_number())
            .add_extension(
            x509.BasicConstraints(
                ca=False,
                path_length=None
            ),
            critical=True,
        )
            .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
            .add_extension(
             x509.SubjectAlternativeName([x509.DNSName(u'localhost')]),
            critical=True,
        )
            .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=True,
                key_agreement=True,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=False,
        )
            .sign(private_key=issuer_rsa, algorithm=hashes.SHA256())
    )

    # Write CA's certificate and server' certificate and private key to file
    with open("ca.cert.pem", "wb") as f:
        f.write(issuer_certificate.public_bytes(serialization.Encoding.PEM))

    with open("server.key.pem", "wb") as f:
        f.write(
            server_rsa.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    with open("server.cert.pem", "wb") as f:
        f.write(server_certificate.public_bytes(serialization.Encoding.PEM))

    # Start an HTTPS endpoint
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain('server.cert.pem', 'server.key.pem')

    httpd = http.server.HTTPServer(('localhost', 443), http.server.SimpleHTTPRequestHandler)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    httpd.serve_forever()