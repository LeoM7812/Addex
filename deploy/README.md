# Deploying Addex

The whole stack (Postgres, Redis, API, crawler, Caddy for HTTPS) runs from
`deploy/docker-compose.yml` on any Linux host with Docker. Stremio only installs remote
addons over HTTPS, so the host needs a public hostname; Caddy gets the certificate.

The steps below use Oracle Cloud's Always Free tier (an ARM VM with 2 OCPU / 12 GB that
stays on 24/7) and a free DuckDNS hostname. Any VPS works the same way from step 4.

## 1. Server (Oracle Cloud)

1. Create an account at <https://www.oracle.com/cloud/free/> (a card is asked for
   identity checks; Always Free resources are not charged).
2. *Compute → Instances → Create instance*:
   - Image: **Ubuntu 24.04**
   - Shape: **VM.Standard.A1.Flex**, 2 OCPU, 12 GB memory (the Always Free limit)
   - Add your SSH public key; keep "assign a public IPv4 address" on.
   - If creation fails with "out of host capacity", retry later or pick another
     availability domain.
3. Open ports 80 and 443: *Networking → Virtual cloud networks → (your VCN) → Security
   lists → Default → Add ingress rules*: source `0.0.0.0/0`, TCP, destination ports
   `80` and `443`.

Oracle may reclaim Always Free instances that stay idle (CPU, network and memory under
20% for a week). Upgrading the account to Pay As You Go removes that rule and keeps
Always Free resources free; set a budget alert if you do.

## 2. Hostname (DuckDNS)

Sign in at <https://www.duckdns.org>, create a subdomain (e.g. `addex`) and set its IP to
the instance's public IP. The hostname is then `addex.duckdns.org`.

## 3. Docker on the server

```sh
ssh ubuntu@<public-ip>
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && exit        # log back in afterwards

# Oracle's Ubuntu images ship an iptables policy that rejects new connections.
sudo iptables -I INPUT 6 -p tcp -m multiport --dports 80,443 -m state --state NEW -j ACCEPT
sudo netfilter-persistent save
```

## 4. Run Addex

```sh
git clone <repo-url> addex && cd addex
cp deploy/.env.example deploy/.env
nano deploy/.env        # ADDEX_DOMAIN=addex.duckdns.org, POSTGRES_PASSWORD=<openssl rand -hex 24>

alias addex-compose='docker compose -f deploy/docker-compose.yml --env-file deploy/.env'
addex-compose up -d --build
```

Load the addon registry and the initial titles (the seeds take about a minute each):

```sh
addex-compose run --rm crawler addex registry sync
addex-compose run --rm crawler addex seed anime
addex-compose run --rm crawler addex seed imdb
addex-compose run --rm crawler addex link anime
```

Check `https://addex.duckdns.org/manifest.json`, then install that URL in Stremio. Titles
outside the seeds are indexed the first time someone opens them.

## Operating

```sh
addex-compose logs -f crawler                         # probes and on-demand checks
addex-compose run --rm crawler addex-crawler status   # queues and per-addon results
git pull && addex-compose up -d --build               # update (migrations run on start)
addex-compose exec postgres pg_dump -U addex addex | gzip > addex-$(date +%F).sql.gz
```

After editing `registry/addons.yaml`, rebuild (`up -d --build`) and run
`addex registry sync` again.
