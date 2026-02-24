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

def access_shared_files(page):
    print("\nAcessando aba de arquivos (Shared)...")
    try:
        # Tenta localizar o botão/aba "Shared" ou "Arquivos"
        shared_tab = page.locator('button[role="tab"][aria-label="Shared"], button[role="tab"][aria-label="Arquivos"], button[role="tab"]:has-text("Shared"), button[role="tab"]:has-text("Arquivos")')
        
        page.wait_for_selector('button[role="tab"]', timeout=15000)
        human_delay(page)
        
        shared_tab.first.click()
        print("Aba de arquivos clicada.")
        
        # O Teams carrega os arquivos dentro de um IFrame do SharePoint
        # Precisamos esperar o IFrame ou os elementos do SharePoint aparecerem
        print("Aguardando carregamento do SharePoint...")
        
        # Seletor genérico para a lista de arquivos do SharePoint/Teams
        # Tentamos detectar tanto no frame principal quanto em possíveis iframes
        file_list_selector = '[data-automationid="DetailsRow"], [role="row"][data-selection-index], .od-ItemEntity-name'
        
        # Loop de espera para garantir que os arquivos apareçam (pode demorar)
        for i in range(10):
            if page.locator(file_list_selector).count() > 0:
                print("Lista de arquivos detectada!")
                return True
            
            # Verifica se há IFrames e tenta olhar dentro deles
            for frame in page.frames:
                if frame.locator(file_list_selector).count() > 0:
                    print("Lista de arquivos detectada dentro de um IFrame!")
                    return True
            
            page.wait_for_timeout(2000)
            print(f"Tentativa {i+1}/10: Aguardando arquivos...")
            
        print("Aviso: Arquivos não detectados após espera.")
        return False
    except Exception as e:
        print(f"Erro ao acessar aba de arquivos: {e}")
        return False

def list_and_download_files(page):
    print("\nAnalisando arquivos para download...")
    try:
        # Vamos buscar os arquivos tanto no frame principal quanto nos IFrames
        all_files = []
        
        # Seletores de linha de arquivo do SharePoint
        row_selector = '[data-automationid="DetailsRow"], [role="row"][data-selection-index]'
        
        # Função interna para extrair dados de um frame
        def extract_from_provider(container):
            rows = container.query_selector_all(row_selector)
            found = []
            for row in rows:
                try:
                    # Tenta pegar o nome do arquivo
                    name_el = row.query_selector('[data-automationid="nameField"], .od-ItemEntity-name, [role="gridcell"] button')
                    if not name_el: continue
                    
                    name = name_el.inner_text().strip()
                    
                    # Tenta pegar a data de modificação (útil para o futuro!)
                    mod_el = row.query_selector('[data-automationid="modifiedField"], .od-ItemEntity-modified')
                    mod_date = mod_el.inner_text().strip() if mod_el else "Desconhecida"
                    
                    found.append({
                        "name": name,
                        "element": name_el,
                        "modified": mod_date
                    })
                except:
                    continue
            return found

        # Busca no frame principal
        all_files.extend(extract_from_provider(page))
        
        # Busca nos IFrames
        for frame in page.frames:
            all_files.extend(extract_from_provider(frame))

        if not all_files:
            print("Nenhum arquivo listado.")
            return False

        print(f"Encontrados {len(all_files)} itens.")
        
        # Para o futuro: Aqui compararemos com um banco de dados local
        # Por enquanto, vamos baixar apenas o primeiro arquivo que não seja uma pasta
        for file_info in all_files:
            name = file_info['name']
            # Evita pastas (geralmente não têm extensão ou têm ícone específico)
            if "." in name:
                print(f"Iniciando download de: {name} (Modificado em: {file_info['modified']})")
                
                try:
                    with page.expect_download(timeout=60000) as download_info:
                        human_delay(page)
                        file_info['element'].click()
                    
                    download = download_info.value
                    os.makedirs("downloads", exist_ok=True)
                    path = os.path.join(os.getcwd(), "downloads", download.suggested_filename)
                    download.save_as(path)
                    print(f"Download finalizado com sucesso!")
                    return True # Baixou um, podemos parar por agora
                except Exception as e:
                    print(f"Falha ao baixar {name}: {e}")
            else:
                print(f"Ignorando pasta: {name}")

        return False
    except Exception as e:
        print(f"Erro ao listar arquivos: {e}")
        return False

def run():
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=False)
        
        if Path(STATE_FILE).exists():
            print("Carregando sessão existente...")
            context = browser.new_context(storage_state=STATE_FILE)
        else:
            print("Nenhuma sessão encontrada. Iniciando novo login...")
            context = browser.new_context()

        page = context.new_page()
        page.goto("https://teams.microsoft.com")

        print("Aguardando carregamento (3 segundos)...")
        page.wait_for_timeout(3000)

        if "login.microsoftonline.com" in page.url or "login.live.com" in page.url:
            login(page)
            print("\n--- AÇÃO NECESSÁRIA ---")
            print("Complete o 2FA manualmente.")
            try:
                page.wait_for_url("https://teams.microsoft.com/**", timeout=300000)
                page.wait_for_timeout(5000)
                context.storage_state(path=STATE_FILE)
                print(f"Sessão salva!")
            except Error:
                print("Timeout no login.")
        
        classes = get_classes(page)
        
        if classes:
            print(f"\nTotal: {len(classes)} classes.")
            # Teste com a primeira classe
            if enter_class(page, classes[0]):
                if access_shared_files(page):
                    list_and_download_files(page)
        
        print("\nScript aguardando encerramento...")
        try:
            page.wait_for_timeout(600000)
        except Error:
            print("Navegador fechado.")

if __name__ == "__main__":
    run()