#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Roda dentro do GitHub Actions (cron a cada 15min).
Puxa Meta + Google Ads + Ploomes das ultimas 24h, le o ultimo valor manual
do TikTok (tiktok_manual.json, atualizado por fora), e gera index.html
com tudo embutido — sem servidor, sem banco, sem estado entre execucoes
alem do que esta commitado no proprio repo.

Credenciais vem de variaveis de ambiente (GitHub Secrets), nunca de arquivo.
"""

import os
import sys
import json
import requests
from datetime import datetime, timedelta

ROOT = os.path.dirname(__file__)

# ============ META ============
def sync_meta():
    token = os.environ.get("META_TOKEN")
    act_id = os.environ.get("META_ACT_ID")
    if not token or not act_id:
        print("[Meta] ERRO variaveis de ambiente ausentes")
        return {"spend": 0, "impressions": 0, "clicks": 0, "conversions": 0}, {"name": "N/A", "spend": 0, "conversions": 0}

    url = f"https://graph.facebook.com/v18.0/act_{act_id}/insights"
    params = {
        "access_token": token,
        "date_preset": "yesterday",
        "fields": "campaign_id,campaign_name,adset_id,adset_name,spend,impressions,clicks,actions",
        "level": "adset"
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json().get("data", [])
        print(f"[Meta] OK {len(data)} registros")

        total_spend = total_impr = total_clicks = total_conv = 0
        top = {"name": "N/A", "spend": 0, "conversions": 0}

        for item in data:
            spend = float(item.get("spend", 0))
            impr = int(item.get("impressions", 0))
            clicks = int(item.get("clicks", 0))
            conv = 0
            for action in item.get("actions", []):
                if action["action_type"] == "messages_conversation_started_7d":
                    conv = int(action.get("value", 0))

            total_spend += spend
            total_impr += impr
            total_clicks += clicks
            total_conv += conv

            if spend > top["spend"]:
                top = {"name": item.get("adset_name", "N/A"), "spend": spend, "conversions": conv}

        return {
            "spend": total_spend, "impressions": total_impr,
            "clicks": total_clicks, "conversions": total_conv
        }, top
    except Exception as e:
        print(f"[Meta] ERRO {e}")
        return {"spend": 0, "impressions": 0, "clicks": 0, "conversions": 0}, {"name": "N/A", "spend": 0, "conversions": 0}

# ============ GOOGLE ADS ============
def sync_google():
    dev_token = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN")
    client_id = os.environ.get("GOOGLE_ADS_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_ADS_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_ADS_REFRESH_TOKEN")
    customer_id = os.environ.get("GOOGLE_ADS_CUSTOMER_ID")

    if not all([dev_token, client_id, client_secret, refresh_token, customer_id]):
        print("[Google] ERRO variaveis de ambiente ausentes")
        return {"spend": 0, "impressions": 0, "clicks": 0, "conversions": 0}

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

        query = """
            SELECT metrics.cost_micros, metrics.clicks, metrics.impressions, metrics.conversions
            FROM campaign
            WHERE campaign.status = 'ENABLED' AND segments.date DURING YESTERDAY
        """
        response = ga_service.search(customer_id=customer_id, query=query)

        spend = impr = clicks = conv = 0
        for row in response:
            spend += row.metrics.cost_micros / 1_000_000
            impr += row.metrics.impressions
            clicks += row.metrics.clicks
            conv += row.metrics.conversions

        print(f"[Google] OK spend={spend:.2f}")
        return {"spend": spend, "impressions": impr, "clicks": clicks, "conversions": conv}
    except Exception as e:
        print(f"[Google] ERRO {e}")
        return {"spend": 0, "impressions": 0, "clicks": 0, "conversions": 0}

# ============ PLOOMES ============
def sync_ploomes():
    user_key = os.environ.get("PLOOMES_USER_KEY")
    if not user_key:
        print("[Ploomes] ERRO variavel de ambiente ausente")
        return 0, 0

    ago_24h = datetime.utcnow() - timedelta(hours=27)
    today = datetime.utcnow() + timedelta(hours=1)
    date_filter = f"CreateDate ge {ago_24h.strftime('%Y-%m-%dT%H:%M:%SZ')} and CreateDate lt {today.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    url = "https://public-api2.ploomes.com/Deals"
    headers = {"User-Key": user_key}
    params = {
        "$filter": date_filter,
        "$select": "Id,Title,CreateDate,StatusId,Amount,ContactId",
        "$top": 500
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        deals = r.json().get("value", [])
        total_value = sum(float(d.get("Amount") or 0) for d in deals)
        print(f"[Ploomes] OK {len(deals)} leads")
        return len(deals), total_value
    except Exception as e:
        print(f"[Ploomes] ERRO {e}")
        return 0, 0

# ============ TIKTOK (manual, le do arquivo commitado) ============
def load_tiktok():
    path = os.path.join(ROOT, "tiktok_manual.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"spend": 0, "impressions": 0, "clicks": 0, "leads": 0, "updated_at": None}

# ============ BUILD ============
def main():
    print(f"[Sync CI] Iniciando - {datetime.now().strftime('%H:%M:%S')}")

    meta_data, top_ad = sync_meta()
    google_data = sync_google()
    ploomes_leads, ploomes_value = sync_ploomes()
    tiktok_data = load_tiktok()

    total_spend = meta_data["spend"] + google_data["spend"] + tiktok_data.get("spend", 0)
    total_leads = meta_data["conversions"] + google_data["conversions"] + ploomes_leads

    data = {
        "timestamp": datetime.now().isoformat(),
        "meta": meta_data,
        "google": google_data,
        "tiktok": tiktok_data,
        "ploomes": {"leads": ploomes_leads, "value": ploomes_value},
        "top_ad": top_ad,
        "totals": {
            "spend": total_spend,
            "leads": total_leads,
            "cpa": total_spend / total_leads if total_leads > 0 else 0
        }
    }

    with open(os.path.join(ROOT, "template.html"), encoding="utf-8") as f:
        template = f.read()
    with open(os.path.join(ROOT, "logo.txt"), encoding="utf-8") as f:
        logo_b64 = f.read().strip()

    html = template.replace("__LOGO_BASE64__", logo_b64)
    html = html.replace("__DASHBOARD_JSON__", json.dumps(data, ensure_ascii=False))

    with open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[Sync CI] index.html gerado, spend total R${total_spend:.2f}, leads {total_leads}")

if __name__ == "__main__":
    main()
