# Dashboard Marketing — Frota Brasil

Painel ao vivo de gasto/leads em Meta Ads, Google Ads e Ploomes, hospedado no GitHub Pages.

- **Ver o dashboard:** `index.html` (publicado via GitHub Pages)
- **Atualiza sozinho** a cada 15min via GitHub Actions (`.github/workflows/sync.yml`), sem depender de nenhum computador ligado.
- **TikTok é manual** — editar `tiktok_manual.json` e dar push quando quiser atualizar (baixo volume, não vale automatizar via API por enquanto).
- Credenciais ficam só em GitHub Secrets (Settings → Secrets and variables → Actions), nunca no código.
