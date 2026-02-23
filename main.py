import os
from playwright.sync_api import sync_playwright, Error
from dotenv import load_dotenv

load_dotenv()

def login(page):
    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")
    
    if not email or not password:
        print("EMAIL ou PASSWORD não encontrados no arquivo .env")
        return False

    print(f"Tentando login automático para {email}...")
    
    try:
        # Preencher email
        page.wait_for_selector('input[type="email"]', timeout=30000)
        page.fill('input[type="email"]', email)
        page.click('#idSIButton9') # Botão "Avançar"
        
        # Preencher senha
        page.wait_for_selector('input[type="password"]', timeout=30000)
        page.fill('input[type="password"]', password)
        page.click('#idSIButton9') # Botão "Entrar"
        
        # Lidar com "Mantenha-se conectado?"
        try:
            page.wait_for_selector('#idSIButton9', timeout=5000)
            page.click('#idSIButton9')
        except:
            pass

        # Lidar com "Use o aplicativo Web em vez disso" (se aparecer)
        try:
            # Tenta encontrar por texto em Português ou Inglês usando regex
            page.locator('text=/Use (o aplicativo Web|the web app) em vez disso|instead/i').click(timeout=10000)
        except:
            pass
            
        print("Login automático concluído. Verifique se o MFA (Autenticador) é necessário.")
        return True
    except Exception as e:
        print(f"Erro durante o login automático: {e}")
        print("Por favor, complete o login manualmente.")
        return False

def run():
    with sync_playwright() as p:
        # Abrindo o navegador (modo visível para o login manual)
        browser = p.firefox.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        print("Indo para o Teams...")
        page.goto("https://teams.microsoft.com")
        
        login(page)
        
        print("O script vai aguardar para você finalizar qualquer ação ou fechar o navegador.")
        # O script vai esperar você fechar o navegador antes de terminar
        try:
            page.wait_for_timeout(600000) # 10 minutos de espera
        except Error:
            print("Navegador fechado, encerrando o script.")

if __name__ == "__main__":
    run()