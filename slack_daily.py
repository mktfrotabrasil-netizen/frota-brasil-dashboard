#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Relatorio diario no Slack, rodando via GitHub Actions (sem depender de
nenhuma sessao do Claude, sem custo de credito).

Janela: semana domingo->sabado, cumulativa desde o domingo ate agora.
Ex: sexta manda domingo-sexta; sabado manda a semana inteira (domingo-sabado).

Roda 17:30 horario de Brasilia (20:30 UTC), configurado no workflow.
"""

import os
import sys
import io
import json
import requests
from datetime import datetime, timedelta

# Reforca UTF-8 na saida (console do Windows local as vezes usa cp1252;
# GitHub Actions/Ubuntu ja e UTF-8 por padrao, isso e so blindagem extra)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CAMPO_PRODUTO = 41216241
CAMPO_ORIGEM_CLIENTE = 42831872


def janela_semana():
    """Domingo 00h (Brasilia) ate agora. Retorna (inicio, fim) em horario de Brasilia."""
    agora = datetime.utcnow() - timedelta(hours=3)
    dias_desde_domingo = (agora.weekday() + 1) % 7  # Monday=0..Sunday=6 -> Sunday=0
    domingo = (agora - timedelta(days=dias_desde_domingo)).replace(hour=0, minute=0, second=0, microsecond=0)
    return domingo, agora


def sync_meta_periodo(inicio, fim):
    token = (os.environ.get("META_TOKEN") or "").strip()
    act_id = (os.environ.get("META_ACT_ID") or "").strip()
    if not token or not act_id:
        return {"spend": 0, "conversas": 0}, {"name": "N/A", "spend": 0, "conversas": 0}

    url = f"https://graph.facebook.com/v18.0/act_{act_id}/insights"
    params = {
        "access_token": token,
        "time_range": json.dumps({"since": inicio.strftime("%Y-%m-%d"), "until": fim.strftime("%Y-%m-%d")}),
        "fields": "campaign_name,adset_name,spend,actions",
        "level": "adset"
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json().get("data", [])

        total_spend = total_conv = 0
        top = {"name": "N/A", "spend": 0, "conversas": 0}
        for item in data:
            spend = float(item.get("spend", 0))
            conv = 0
            for a in item.get("actions", []):
                if a["action_type"] == "onsite_conversion.messaging_conversation_started_7d":
                    conv = int(a.get("value", 0))
            total_spend += spend
            total_conv += conv
            if spend > top["spend"]:
                top = {"name": item.get("adset_name", "N/A"), "spend": spend, "conversas": conv}

        print(f"[Meta] OK spend={total_spend:.2f} conversas={total_conv}")
        return {"spend": total_spend, "conversas": total_conv}, top
    except Exception as e:
        print(f"[Meta] ERRO {e}")
        return {"spend": 0, "conversas": 0}, {"name": "N/A", "spend": 0, "conversas": 0}


def sync_google_periodo(inicio, fim):
    dev_token = (os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN") or "").strip()
    client_id = (os.environ.get("GOOGLE_ADS_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GOOGLE_ADS_CLIENT_SECRET") or "").strip()
    refresh_token = (os.environ.get("GOOGLE_ADS_REFRESH_TOKEN") or "").strip()
    customer_id = (os.environ.get("GOOGLE_ADS_CUSTOMER_ID") or "").strip()

    if not all([dev_token, client_id, client_secret, refresh_token, customer_id]):
        return {"spend": 0, "conversoes": 0}

    try:
        from google.ads.googleads.client import GoogleAdsClient

        config = {
            "developer_token": dev_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "use_proto_plus": True,
        }
        client = GoogleAdsClient.load_from_dict(config)
        ga_service = client.get_service("GoogleAdsService")

        query = f"""
            SELECT metrics.cost_micros, metrics.conversions
            FROM campaign
            WHERE campaign.status = 'ENABLED'
              AND segments.date BETWEEN '{inicio.strftime("%Y-%m-%d")}' AND '{fim.strftime("%Y-%m-%d")}'
        """
        response = ga_service.search(customer_id=customer_id, query=query)

        spend = conv = 0
        for row in response:
            spend += row.metrics.cost_micros / 1_000_000
            conv += row.metrics.conversions

        print(f"[Google] OK spend={spend:.2f} conversoes={conv}")
        return {"spend": spend, "conversoes": conv}
    except Exception as e:
        print(f"[Google] ERRO {e}")
        return {"spend": 0, "conversoes": 0}


def prop(registro, field_id):
    for p in registro.get("OtherProperties") or []:
        if p.get("FieldId") == field_id:
            return p.get("ObjectValueName") or p.get("StringValue")
    return None


def sync_ploomes_periodo(inicio, fim):
    """inicio/fim em horario de Brasilia; CreateDate no Ploomes filtra em UTC (+3h)."""
    user_key = (os.environ.get("PLOOMES_USER_KEY") or "").strip()
    if not user_key:
        return 0, 0.0, {}

    inicio_utc = inicio + timedelta(hours=3)
    fim_utc = fim + timedelta(hours=3)
    date_filter = (
        f"CreateDate ge {inicio_utc.strftime('%Y-%m-%dT%H:%M:%SZ')} "
        f"and CreateDate lt {fim_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )

    url = "https://public-api2.ploomes.com/Deals"
    headers = {"User-Key": user_key}
    params = {
        "$filter": date_filter,
        "$select": "Id,Title,CreateDate,StatusId,Amount",
        "$expand": "OtherProperties",
        "$top": 500
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        deals = r.json().get("value", [])

        total_value = sum(float(d.get("Amount") or 0) for d in deals)
        produtos = {}
        for d in deals:
            p = prop(d, CAMPO_PRODUTO) or "(sem produto informado)"
            produtos[p] = produtos.get(p, 0) + 1

        print(f"[Ploomes] OK {len(deals)} leads")
        return len(deals), total_value, produtos
    except Exception as e:
        print(f"[Ploomes] ERRO {e}")
        return 0, 0.0, {}


def fmt_money(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def load_tiktok():
    try:
        with open(os.path.join(os.path.dirname(__file__), "tiktok_manual.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"spend": 0, "leads": 0}


def montar_mensagem(inicio, fim, meta, top_ad, google, ploomes_leads, ploomes_value, produtos):
    dia_semana = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"][fim.weekday()]
    periodo = f"{inicio.strftime('%d/%m')} a {fim.strftime('%d/%m')}" if inicio.date() != fim.date() else fim.strftime('%d/%m')

    tiktok = load_tiktok()
    gasto_total = meta["spend"] + google["spend"] + tiktok.get("spend", 0)
    meta_conv = int(meta["conversas"])
    google_conv = int(google["conversoes"])
    tiktok_leads = int(tiktok.get("leads", 0))
    leads_midia_paga = meta_conv + google_conv + tiktok_leads

    produtos_txt = "\n".join(f"• {nome}: {qtd}" for nome, qtd in sorted(produtos.items(), key=lambda x: -x[1])) or "• (nenhum lead no período)"

    texto = (
        f"*📊 Resumo {dia_semana} — {periodo}*\n\n"
        f"💰 *Gasto Total:* {fmt_money(gasto_total)}\n"
        f"   Meta: {fmt_money(meta['spend'])} · Google: {fmt_money(google['spend'])} · TikTok: {fmt_money(tiktok.get('spend', 0))}\n\n"
        f"🎯 *Leads Ploomes:* {ploomes_leads} ({fmt_money(ploomes_value)} em pipeline)\n"
        f"📱 *Leads via mídia paga:* {leads_midia_paga} (Meta {meta_conv} + Google {google_conv} + TikTok {tiktok_leads})\n\n"
        f"🏆 *Top anúncio (Meta):* {top_ad['name']}\n"
        f"   Gasto {fmt_money(top_ad['spend'])} · {int(top_ad['conversas'])} conversas\n\n"
        f"📦 *Produtos que entraram:*\n{produtos_txt}"
    )
    return texto


def enviar_slack(texto):
    webhook = (os.environ.get("SLACK_WEBHOOK_URL") or "").strip()
    if not webhook:
        print("[Slack] ERRO SLACK_WEBHOOK_URL ausente")
        return
    try:
        r = requests.post(webhook, json={"text": texto}, timeout=15)
        r.raise_for_status()
        print("[Slack] OK mensagem enviada")
    except Exception as e:
        print(f"[Slack] ERRO {e}")


def main():
    inicio, fim = janela_semana()
    print(f"[Slack Daily] Janela: {inicio} -> {fim}")

    meta, top_ad = sync_meta_periodo(inicio, fim)
    google = sync_google_periodo(inicio, fim)
    ploomes_leads, ploomes_value, produtos = sync_ploomes_periodo(inicio, fim)

    texto = montar_mensagem(inicio, fim, meta, top_ad, google, ploomes_leads, ploomes_value, produtos)
    print(texto)
    enviar_slack(texto)


if __name__ == "__main__":
    main()
