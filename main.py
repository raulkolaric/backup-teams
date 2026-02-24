import os
import random
from pathlib import Path
from playwright.sync_api import sync_playwright, Error
from dotenv import load_dotenv

load_dotenv(override=True)

STATE_FILE = "state.json"

def human_delay(page):
    delay = random.uniform(0.3, 1.4) * 1000
    page.wait_for_timeout(delay)

def login(page):
    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")
    
    if not email or not password:
        print("ERRO: EMAIL ou PASSWORD não encontrados no ambiente!")
        return False

    print(f"Tentando preenchimento automático para {email}...")
    
    try:
        # Preencher email
        page.wait_for_selector('input[type="email"]', timeout=30000)
        human_delay(page)
        page.fill('input[type="email"]', email)
        human_delay(page)
        page.click('#idSIButton9') # Botão "Avançar"
        
        # Preencher senha
        page.wait_for_selector('input[type="password"]', timeout=30000)
        human_delay(page)
        page.click('input[type="password"]') # Garante o foco no campo
        page.fill('input[type="password"]', password)
        
        # Espera extra específica após preencher a senha para o botão "Entrar" habilitar/processar
        page.wait_for_timeout(1500)
        
        human_delay(page)
        page.click('#idSIButton9') # Botão "Entrar"
        
        # Lidar com "Mantenha-se conectado?"
        try:
            page.wait_for_selector('#idSIButton9', timeout=5000)
            human_delay(page)
            page.click('#idSIButton9')
        except:
            pass

        # Lidar com "Use o aplicativo Web em vez disso" (se aparecer)
        try:
            human_delay(page)
            page.locator('text=/Use (o aplicativo Web|the web app) em vez disso|instead/i').click(timeout=10000)
        except:
            pass
            
        return True
    except Exception as e:
        print(f"Aviso: Preenchimento automático falhou ou já estava logado: {e}")
        return False

def run():
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=False)
        
        # Verifica se já temos uma sessão salva
        if Path(STATE_FILE).exists():
            print("Carregando sessão existente...")
            context = browser.new_context(storage_state=STATE_FILE)
        else:
            print("Nenhuma sessão encontrada. Iniciando novo login...")
            context = browser.new_context()

        page = context.new_page()
        print("Indo para o Teams...")
        page.goto("https://teams.microsoft.com")

        # Aguarda a página carregar/redirecionar
        print("Aguardando carregamento (3 segundos)...")
        page.wait_for_timeout(3000)

        # Se detectarmos que estamos na página de login
        if "login.microsoftonline.com" in page.url or "login.live.com" in page.url:
            print(f"Página de login detectada: {page.url}")
            login(page)
            print("\n--- AÇÃO NECESSÁRIA ---")
            print("Complete o 2FA e o login manualmente no navegador.")
            print("Assim que estiver dentro do Teams, a sessão será salva automaticamente.")
            
            # Espera o usuário chegar no Teams (ou fechar o navegador)
            try:
                # Espera até que a URL seja do Teams (não seja login)
                page.wait_for_url("https://teams.microsoft.com/**", timeout=300000)
                
                # Salva o estado para a próxima vez
                context.storage_state(path=STATE_FILE)
                print(f"Sessão salva com sucesso em {STATE_FILE}!")
            except Error:
                print("Tempo esgotado ou navegador fechado antes de completar o login.")
        elif "teams.microsoft.com" in page.url:
            print("Já logado via sessão recuperada!")
        else:
            print(f"URL atual: {page.url}")

        print("\nO script está ativo. Pressione Ctrl+C no terminal ou feche o navegador para encerrar.")
        
        try:
            # Mantém aberto para você interagir
            page.wait_for_timeout(600000) # 10 minutos
        except Error:
            print("Navegador fechado.")

if __name__ == "__main__":
    run()