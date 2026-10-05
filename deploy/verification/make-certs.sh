#!/bin/sh
# Certificates for the verification identity provider (P11.3; LOCAL VERIFICATION ONLY).
#
# A throwaway CA and a server certificate for the names the provider is reached by: `idp`
# (the API instances, on the private network) and `localhost` (the verification browser
# on the host, through the published 127.0.0.1:9443). Written to deploy/.verification/
# (git-ignored), never into an image. The API instances trust this CA only in the
# verification overlay (SSL_CERT_FILE, deploy/compose.idp.yaml).
#
# The private key is world-readable on purpose: the provider runs as a non-root UID that
# does not own the bind-mounted file. It protects nothing but this local drill.
set -eu
dir="$(cd "$(dirname "$0")/.." && pwd)/.verification"
mkdir -p "$dir"
cd "$dir"
if [ -s ca.crt ] && [ -s idp.crt ] && [ -s idp.key ]; then
  echo "verification certificates already in $dir"
  exit 0
fi
openssl ecparam -name prime256v1 -genkey -noout -out ca.key 2>/dev/null
openssl req -x509 -new -key ca.key -sha256 -days 30 -subj "/CN=Lattice verification CA" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign" \
  -out ca.crt
openssl ecparam -name prime256v1 -genkey -noout -out idp.key 2>/dev/null
openssl req -new -key idp.key -subj "/CN=idp" -out idp.csr
printf 'subjectAltName=DNS:idp,DNS:localhost\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n' > idp.ext
openssl x509 -req -in idp.csr -CA ca.crt -CAkey ca.key -CAcreateserial -sha256 -days 30 \
  -extfile idp.ext -out idp.crt 2>/dev/null
rm -f idp.csr idp.ext ca.srl ca.key
chmod 644 ca.crt idp.crt idp.key
echo "wrote $dir/ca.crt, idp.crt, idp.key (30 days)"
