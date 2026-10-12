# Optional RSA-encrypted SNMP transport

Hookah can encrypt complete SNMPv1/v2c packets in both directions. This includes
GET, WALK, SET, capture, and remote collection-configuration commands.

The transport uses **RSA-OAEP/SHA-256** to wrap a fresh **AES-256-GCM** session key
for each datagram. **RSA-PSS/SHA-256** signatures authenticate each envelope.
Packet IDs, request/response correlation, and a timestamp/replay window protect
packet handling. RSA's small direct-encryption limit therefore does not restrict
normal multi-object SNMP responses.

## 1. Generate keys on the Manager

Activate the project environment, then:

```bash
./snmp_data_collector_v12.py keys generate --directory keys --bits 3072
```

The manager generates:

```text
keys/manager_private.pem
keys/manager_public.pem
keys/manager_encryption.json
```

Existing files are preserved; generating into a directory already containing
these files fails. Supported key sizes are 2048, 3072 (default), and 4096 bits.

The generated encryption profile references the private key beside it:

```json
{
  "enabled": true,
  "private_key_file": "manager_private.pem",
  "peer_public_key_file": null
}
```

The manager's interactive menu also offers **Generate manager RSA keys** and
**Configure RSA-encrypted traffic**.

## 2. Explicitly configure the Agent's private key

This default setup follows the shared-private-key layout: copy the manager's
generated `manager_private.pem` onto the monitored device, then explicitly set
its path. Both endpoints use that private key to decrypt incoming traffic and
derive its public key to encrypt outgoing traffic.

Linux example:

```bash
./snmp_agent.py config set --config snmp_agent_config.json \
  --encryption on --rsa-private-key /opt/hookah/keys/manager_private.pem
```

Windows example (administrator PowerShell, for an installed service):

```powershell
$python = "C:\Program Files\Hookah Agent\.venv\Scripts\python.exe"
$agent = "C:\Program Files\Hookah Agent\snmp_agent.py"
& $python $agent config set --config "C:\ProgramData\Hookah\Agent\snmp_agent_config.json" `
    --encryption on --rsa-private-key "C:\ProgramData\Hookah\Agent\keys\manager_private.pem"
Restart-Service HookahSnmpAgent
```

The saved Agent configuration contains an explicit decryption-key reference:

```json
{
  "encryption": {
    "enabled": true,
    "private_key_file": "C:\\ProgramData\\Hookah\\Agent\\keys\\manager_private.pem",
    "peer_public_key_file": null
  }
}
```

Paths are resolved relative to the configuration file when they are not
absolute. Enabling/disabling encryption or changing keys requires an Agent
restart. The service account must be able to read the PEM; the Windows installer
sets the application/data permissions and can copy the explicitly provided PEM.

`peer_public_key_file` is optional. When supplied, outgoing encryption and
incoming signature verification use that peer public key, allowing separately
generated endpoint private keys. With `null`, the shared key's public key is
derived automatically.

## 3. Query the encrypted Agent

```bash
./snmp_data_collector_v12.py get --target udp:192.168.1.20:1161 \
  --community public --oid .1.3.6.1.2.1.1.1.0 \
  --encryption-config keys/manager_encryption.json

./snmp_data_collector_v12.py walk --target udp:192.168.1.20:1161 \
  --community public --oid .1.3.6.1.2.1.2.2.1.2 \
  --encryption-config keys/manager_encryption.json

./snmp_data_collector_v12.py capture --target udp:192.168.1.20:1161 \
  --community public --oid .1.3.6.1.2.1.2.2.1.10.2 --sync-agent \
  --output incoming.csv --encryption-config keys/manager_encryption.json
```

Replace the IP/interface index with those of the monitored device.

The Manager adapts its existing Net-SNMP tools through a temporary loopback UDP
proxy. The packets between devices are encrypted; ordinary SNMP decoding and
metric conversion happen locally after decryption. The encrypted format is a
Hookah envelope around SNMP, rather than a new standard SNMP security version.
An encrypted Agent accepts the Hookah encrypted transport on its configured
port and rejects ordinary plaintext requests on that port.

## 4. Remotely change collection intervals through the encrypted transport

```bash
./snmp_agent.py config set --remote udp:192.168.1.20:1161 \
  --community lab-write --interval ifInOctets 120 \
  --encryption-config keys/manager_encryption.json
```

The existing manager-IP restrictions and SNMP read/write community permissions
still apply. Collection periods continue to reload live. Remote configuration
responses contain collection settings, not PEM material or key paths.

Timestamp validation permits a 120-second clock difference between devices.
The Windows service uses system time; keep the devices' clocks synchronized.

Disable encrypted traffic locally:

```bash
./snmp_agent.py config set --config snmp_agent_config.json --encryption off
```

Restart the Agent and omit `--encryption-config` from Manager commands to use
ordinary SNMP again.
