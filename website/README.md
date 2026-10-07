# Cardano Card website

Next.js App Router landing page and interactive shopping chat for the Cardano Card demo.

## Run locally

Use Node.js 22.9 or later.

```sh
cd website
npm ci
npm run dev
```

Open http://127.0.0.1:5175. Run `npm run build` to produce the static site in `out/`.
For a production preview, serve `out/` with any static web server, for example:

```sh
python3 -m http.server 5174 --bind 127.0.0.1 --directory out
```

## Included

- Responsive landing page inspired by the messaging-first layout of Fliptexts, with original imagery and Cardano Card copy.
- Masumi-inspired pink branding, blush surfaces, and an original generated pink salt-lake background.
- React chat demo with sample shopping requests, a spending ceiling, quote approval, simulated escrow funding, payout/refund outcomes, and downloadable demo receipts.
- Keyboard-accessible dialog, mobile layout, and reduced-motion support.

The chat runs entirely in the browser. It does not send SMS, search merchants, call AgentCard, open a wallet, or move funds. All quotes and settlements are explicitly illustrative. The demo uses a separate 20 tUSDM test escrow amount and a USD merchant spending ceiling.

## Connect the real system

Replace simulated transitions in `components/demo-chat.jsx` with authenticated server endpoints for job creation, quotes, payment instructions, and status updates. Keep AgentCard credentials and Masumi operations on the server. Drive the journey from verified backend events, including unresolved checkout outcomes and settlement delays. Add an SMS provider separately if actual text messaging is part of the demo.

## Files

- `app/`: Next.js layout, route, styles, and icon.
- `components/landing.jsx`: landing page and demo entry points.
- `components/phone-preview.jsx`: static iPhone conversation artwork modeled on the supplied reference.
- `components/demo-chat.jsx`: browser-local demo state and receipts.
- `public/assets/pink-salt-lake.png`: current generated nature background.
- `public/assets/ocean.png`: original generated ocean image, preserved for reuse.
- `design/image-prompts.json`: image-generation prompt and provenance.
- `.openai/hosting.json`: private Sites project and static export settings.

## Railway

Public site: https://cardanocard-website-production.up.railway.app

The `cardanocard-website` service in the existing `cardanocard-preprod` project builds this Next.js app with Node.js 24 and serves its static export through Caddy. Railway terminates HTTPS; Caddy listens on `PORT` (8080 by default). The deployment includes only the website and needs no backend credentials.

To publish local changes, run from the repository root:

```sh
railway up ./website --path-as-root \
  --project b7379319-a841-459c-ae44-e1335c7eee89 \
  --environment 2a526083-0e1d-42f9-9605-274c57bce885 \
  --service 3fe1483c-8985-4843-ad7b-dc66cf8707ac \
  --detach --message "Update website"
```

Check the deployment's final status in Railway before treating the update as live. This service currently uses local uploads; GitHub pushes do not automatically redeploy it.
