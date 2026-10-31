# alexaSkill jee2alexa


Skill **Alexa SmartHome** Alexa-hosted pour le plugin Jeedom [`alexaapiv2`](https://github.com/limad/jeedom_alexaPremium).

Expose les équipements Jeedom natifs (Z-Wave, Zigbee, virtuels, etc.) à Alexa via :

```
Alexa directive
  ↓ Lambda Python (ce repo)
    ↓ POST + Bearer (Account Linking OAuth)
      ↓ Jeedom: smartHomeProxy.php
        ↓ cmd::byId(cookie).execCmd()
```

Le Lambda est un **proxy mince** — toute la logique métier (Discovery, Control, ReportState) est en PHP côté Jeedom (`core/php/smartHomeProxy.php`).

## Architecture

| Fichier | Rôle |
|---|---|
| `skill.json` | Manifest Alexa SmartHome v3 (apis, events, locales) |
| `accountLinking.json` | Template OAuth 2.0 Auth Code Grant — placeholders remplacés par le plugin lors du déploiement |
| `lambda/lambda_function.py` | Handler Python — forward des directives Alexa au proxy PHP, gère AcceptGrant localement |
| `lambda/requirements.txt` | `urllib3` |

## Placeholders injectés au déploiement

| Placeholder | Source | Fichier |
|---|---|---|
| `{{LAMBDA_ARN}}` | auto-renseigné par Amazon après création | `skill.json` |
| `{{JEEDOM_URL}}` | `network::getNetworkAccess('external')` | `lambda/lambda_function.py` |
| `{{AUTHORIZE_URL}}` / `{{TOKEN_URL}}` | `JEEDOM_URL + /plugins/alexaapiv2/core/php/oauth/{authorize,token}.php` | `accountLinking.json` |
| `{{OAUTH_CLIENT_ID}}` / `{{OAUTH_CLIENT_SECRET}}` | auto-générés par `_oauth_helper.php` | `accountLinking.json` |
| `{{PRIVACY_URL}}` / `{{TOS_URL}}` | URLs documentation plugin | `skill.json` |

Le déploiement automatique est piloté par l'action AJAX `deploySmartHomeSkill` du plugin — voir [`docs/smarthome_skill_user_guide.md`](https://github.com/limad/jeedom_alexaPremium/blob/alpha/docs/smarthome_skill_user_guide.md) côté plugin.

## Capabilities Alexa supportées

| Capability | generic_type Jeedom |
|---|---|
| `Alexa.PowerController` | `LIGHT_ON`/`LIGHT_OFF` ou `ENERGY_ON`/`ENERGY_OFF` ou `LIGHT_SLIDER` |
| `Alexa.BrightnessController` | `LIGHT_SLIDER` / `LIGHT_SET_BRIGHTNESS` |
| `Alexa.ColorController` | `LIGHT_SET_COLOR` |
| `Alexa.ColorTemperatureController` | `LIGHT_SET_COLOR_TEMP` |
| `Alexa.ThermostatController` | `THERMOSTAT_SET_SETPOINT` (+ `THERMOSTAT_SETPOINT`, `THERMOSTAT_MODE`) |
| `Alexa.LockController` | `LOCK_CLOSE` + `LOCK_OPEN` |

## CI

Workflow GitHub Actions (`.github/workflows/test.yml`) :
- Syntax-check Python sur 3.10, 3.11, 3.12
- Tests `pytest` (si présents dans `lambda/test/`)
- Validation JSON `skill.json` + `accountLinking.json`
- Sanity-check manifest (apis.smartHome, events, accountLinking AUTH_CODE)

## Licence

AGPL-3.0 — même licence que le plugin alexaapiv2.
