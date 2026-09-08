# Browser Companion

Generyczny add-on Home Assistant: **Chromium w sidebarze**, w którym użytkownik sam się loguje. Integracja przez API mówi, **na jakie zdarzenie czekać** (np. HTTP 302 z `Location: app://…`). Add-on zwraca przechwycony URL i query.

Nie rozwiązuje captchy i nie wypełnia formularzy.

## Wymagania

- Home Assistant OS albo Supervised (add-ony). **Nie działa** na HA Container / Core.
- Około **1 GB RAM** na Chromium.

## Instalacja

1. Ustawienia → Dodatki → Sklep → ⋮ → **Repositories**
2. Dodaj URL tego repozytorium
3. Zainstaluj **Browser Companion** i uruchom
4. W sidebarze pojawi się panel Chromium

Po aktualizacji: Dodatki → Browser Companion → **Rebuild** (albo odinstaluj i zainstaluj ponownie). W logach po starcie powinno być `loading service 'companion'` oraz `[companion] starting session API on :8100`.

## API (dla integracji)

Baza: `http://{hostname}:8100`  
Hostname: slug add-onu z `_` zamienionym na `-` (`local-browser-companion` albo `{hash}-browser-companion`). Slug kończy się na `browser_companion`.

Jedna sesja naraz. Kolejny `POST` zastępuje poprzednią.

### `POST /v1/sessions`

Pole `wait` (obiekt albo lista) opisuje, czego integracja oczekuje. Add-on nic nie zgaduje.

**HTTP 302 + Location** (Biedronka):

```json
{
  "client_id": "biedronka",
  "start_url": "https://konto.biedronka.pl/realms/loyalty/protocol/openid-connect/auth?…",
  "wait": {
    "event": "http_redirect",
    "status_codes": [302],
    "location_prefixes": ["app://cma20.biedronka.pl"]
  },
  "timeout_seconds": 600
}
```

Inne `event`:

| `event` | Kiedy | Pola |
| --- | --- | --- |
| `http_redirect` | Odpowiedź 3xx z nagłówkiem `Location` | `status_codes` (domyślnie 301/302/303/307/308), `location_prefixes` i/lub `location_schemes` |
| `navigation` | Przejście strony na URL | `url_prefixes` i/lub `url_schemes` |
| `protocol_handler` | Chromium oddaje custom scheme do OS (`xdg-open`) | `schemes` |

Lista `wait` = pierwsze pasujące zdarzenie wygrywa.

Odpowiedź: `{ "id", "status": "pending", "client_id" }`

### `GET /v1/sessions/{id}`

`pending` | `captured` | `expired` | `error`

Przy `captured`:

```json
{
  "id": "…",
  "status": "captured",
  "client_id": "biedronka",
  "url": "app://cma20.biedronka.pl?code=…",
  "query": { "code": "…" },
  "event": "http_redirect",
  "status_code": 302
}
```

### `DELETE /v1/sessions/{id}`

Anuluje sesję.

### `GET /v1/health`

`{ "ok": true, "session": { … } | null }`

Klient dla custom components: [`examples/homeassistant_client.py`](examples/homeassistant_client.py).

## Lokalny test bez Supervisor

```bash
docker compose up --build
```

Chromium: http://localhost:5800  
API: http://localhost:8100

```bash
curl -s http://localhost:8100/v1/health
```

## Licencja

MIT. Niepowiązane z Google ani z Home Assistant.
