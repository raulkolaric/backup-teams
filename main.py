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

def get_classes(page):
    print("\nBuscando exclusivamente suas Classes (Turmas)...")
    
    try:
        # Alvo: O painel específico de Classes/Aulas do Teams EDU
        class_section_selector = '[data-tid="ClassTeamsSection-panel"]'
        
        try:
            # Espera o painel de classes carregar
            page.wait_for_selector(class_section_selector, timeout=15000)
        except:
            print("Aviso: Painel de classes 'ClassTeamsSection-panel' não detectado.")
            return []

        # Rola a página para garantir o carregamento de todas
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        # Agora buscamos as cartas APENAS dentro do painel de classes
        class_section = page.query_selector(class_section_selector)
        if not class_section:
            return []

        cards = class_section.query_selector_all('.fui-Card')
        
        classes = []
        print(f"Encontradas {len(cards)} classes acadêmicas.")

        for card in cards:
            try:
                # O nome está dentro de um botão com data-testid="team-name"
                name_el = card.query_selector('[data-testid="team-name"]')
                if not name_el:
                    continue
                
                name = name_el.inner_text().strip()
                
                # O ID está no data-tid do card
                tid = card.get_attribute("data-tid") or ""
                team_id = tid.replace("-team-card", "")

                classes.append({
                    "name": name,
                    "id": team_id,
                    "tid": tid
                })
                print(f" - {name}")
            except Exception as e:
                print(f"Erro ao processar card: {e}")

        return classes

    except Exception as e:
        print(f"Erro ao buscar classes: {e}")
        return []

def enter_class(page, team_class):
    print(f"\nEntrando na classe: {team_class['name']}...")
    try:
        # Localiza o card pelo data-tid que salvamos
        card_selector = f'[data-tid="{team_class["tid"]}"]'
        page.wait_for_selector(card_selector, timeout=10000)
        
        human_delay(page)
        # Clica no card para entrar
        page.click(card_selector)
        
        # Espera a navegação (o Teams muda a URL quando você entra em um time)
        page.wait_for_load_state("networkidle")
        print("Sucesso ao entrar na classe.")
        return True
    except Exception as e:
        print(f"Erro ao entrar na classe: {e}")
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

        # Agora que estamos logados, buscamos as classes
        classes = get_classes(page)
        
        if classes:
            print(f"\nTotal de classes encontradas: {len(classes)}")
            # Entra na primeira classe da lista para testar a navegação
            enter_class(page, classes[0])
        else:
            print("\nNenhuma classe encontrada ou erro no seletor.")

        print("\nO script está ativo. Pressione Ctrl+C no terminal ou feche o navegador para encerrar.")
        
        try:
            # Mantém aberto para você interagir
            page.wait_for_timeout(600000) # 10 minutos
        except Error:
            print("Navegador fechado.")

if __name__ == "__main__":
    run()