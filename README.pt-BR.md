# DXMaps AIS reporter

[English](README.md)

Recebe sentenças NMEA AIS do [AIS-catcher](https://github.com/jvde-github/AIS-catcher) por UDP e reporta navios
de longa distância ("DX") para o [DXMaps](https://www.dxmaps.com/aisdxaggregator.html). Substitui o programa
Windows *AIS DX Aggregator* e foi feito para rodar em uma Raspberry Pi.

## Como funciona

```
Receptor AIS ──> AIS-catcher ──UDP/NMEA──> ais_dxmaps.py ──UDP──> www.dxmaps.com:7350
                 (-u 127.0.0.1 7000)       (filtro de distância)
```

Para cada relatório de posição (mensagens tipo 1, 2, 3 e 18) o script calcula a distância até a sua estação. Navios
a pelo menos `min_km` de distância são enviados ao DXMaps em um datagrama UDP:

```
AIS|<indicativo>|<seu locator>|<qrg>|<mmsi>|<locator do navio>|<rep>|<versão>|<nome>
AIS|PU5FON|GG52TM|161975|710000001|GG87JC|0|1.15|
```

Esse formato foi obtido por engenharia reversa do `AisDXAggregator.exe` (guardado em `original/`) e confirmado
com um teste real. O `qrg` é `161975` para o canal AIS A e `162025` para o canal B. Nos spots reais o nome vai vazio.

Filtros aplicados (mesma ideia do programa original): latitude/longitude válidas, MMSI diferente de zero,
mensagens repetidas (retransmitidas) ignoradas, a menos que permitidas, saltos de posição acima de 100 km em menos
de 30 minutos rejeitados e no máximo um spot por MMSI a cada `resend_min` minutos. Mensagens fragmentadas
(várias sentenças), incluindo o tipo 19, são ignoradas.

## Instalação na Raspberry Pi

Requer Raspberry Pi OS ou Debian com systemd e acesso à internet.

```bash
scp -r dxmaps pi@raspberrypi.local:~/      # ou copie a pasta como preferir
ssh pi@raspberrypi.local
cd ~/dxmaps
sudo ./install.sh
```

O instalador:

1. instala o `curl` e o [uv](https://docs.astral.sh/uv/) (em `/usr/local/bin`);
2. cria o usuário de sistema `dxmaps`;
3. copia o script para `/opt/dxmaps/` e pergunta seu indicativo e locator, gravando `/etc/dxmaps/dxmaps.conf`
   (um arquivo existente nunca é sobrescrito);
4. baixa a dependência Python (`pyais`) uma vez;
5. instala, habilita e inicia o serviço systemd `dxmaps`.

Para instalar sem perguntas: `sudo DXMAPS_CALLSIGN=PU5FON DXMAPS_LOCATOR=GG52TM ./install.sh`.

Depois, aponte o AIS-catcher para o script. Na linha de comando, adicione `-u 127.0.0.1 7000`; na interface web,
crie uma saída UDP com host `127.0.0.1` e porta `7000` (formato NMEA).

## Configuração

`/etc/dxmaps/dxmaps.conf` (modelo: `dxmaps.conf.example`). Reinicie após alterar: `sudo systemctl restart dxmaps`.

| Seção | Chave | Padrão | Significado |
|---|---|---|---|
| `[station]` | `callsign` | – | Seu indicativo / ID de estação, 1 a 8 caracteres |
| `[station]` | `locator` | – | Seu locator Maidenhead de 6 caracteres (ex.: `GG52TM`) |
| `[listen]` | `host`, `port` | `127.0.0.1`, `7000` | Endereço UDP onde o AIS-catcher envia |
| `[dxmaps]` | `host`, `port` | `www.dxmaps.com`, `7350` | Destino |
| `[dxmaps]` | `version` | `1.15` | Versão enviada ao DXMaps |
| `[filters]` | `min_km` | `300` | Distância mínima para reportar |
| `[filters]` | `max_km` | `3000` | Distâncias maiores são tratadas como erro de decodificação |
| `[filters]` | `resend_min` | `15` | Minutos entre spots do mesmo MMSI |
| `[filters]` | `allow_repeaters` | `false` | Reportar também mensagens retransmitidas |

Sua posição é o centro do quadrado do locator (cerca de ±4 km com 6 caracteres).

## Operação

```bash
systemctl status dxmaps          # estado
journalctl -u dxmaps -f          # log ao vivo: uma linha "SEND ..." por navio reportado
sudo systemctl restart dxmaps    # após editar a configuração
```

Teste sem instalar (precisa do [uv](https://docs.astral.sh/uv/)):

```bash
cp dxmaps.conf.example dxmaps.conf          # edite indicativo e locator
uv run ais_dxmaps.py --dry-run -v           # mostra o que seria enviado
uv run ais_dxmaps.py --test-ship 710000001 -22.90 -43.17 NAVIOTESTE   # envia UM spot falso (envio real!)
```

O `--test-ship` publica um navio fictício no mapa público; use com moderação e com um MMSI inventado.

## Desinstalação

```bash
sudo systemctl disable --now dxmaps
sudo rm -rf /etc/systemd/system/dxmaps.service /opt/dxmaps /etc/dxmaps /var/lib/dxmaps
sudo userdel dxmaps
```

## Arquivos

| Arquivo | Função |
|---|---|
| `ais_dxmaps.py` | O reporter (arquivo único, dependências declaradas no próprio arquivo para o uv) |
| `dxmaps.conf.example` | Modelo de configuração |
| `dxmaps.service` | Unidade systemd |
| `install.sh` | Instalador para Raspberry Pi |
| `original/` | O programa Windows original e um script Python de teste anterior |
