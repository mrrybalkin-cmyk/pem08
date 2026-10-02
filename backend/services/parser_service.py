"""
Сервис для парсинга веб-страниц через Selenium Chrome
"""
import base64
import asyncio
import time
import logging
from typing import Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

from backend.config import settings

# Логгер для сервиса
logger = logging.getLogger("competitor_monitor.parser")


class ParserService:
    """Парсинг веб-страниц через Chrome с созданием скриншота"""
    
    def __init__(self):
        logger.info("=" * 50)
        logger.info("Инициализация Parser сервиса")
        logger.info(f"  Timeout: {settings.parser_timeout} сек")
        logger.info(f"  User-Agent: {settings.parser_user_agent[:50]}...")
        
        self.timeout = settings.parser_timeout
        self._executor = None
        
        logger.info("Parser service initialized (executor is lazy)")
        logger.info("=" * 50)
    
    def _create_driver(self) -> webdriver.Chrome:
        """Создать новый экземпляр Chrome драйвера"""
        logger.info("  🌐 Создание Chrome драйвера...")
        start_time = time.time()
        
        options = Options()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument(f'--user-agent={settings.parser_user_agent}')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option('excludeSwitches', ['enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)
        
        logger.debug("  Опции Chrome настроены")
        logger.info("  📥 Загрузка ChromeDriver...")
        
        # Автоматическая установка ChromeDriver
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        
        elapsed = time.time() - start_time
        logger.info(f"  ✓ Chrome драйвер создан за {elapsed:.2f} сек")
        
        return driver
    
    def _parse_sync(self, url: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[bytes], Optional[str]]:
        """
        Синхронный парсинг URL (выполняется в отдельном потоке)
        """
        logger.info("=" * 50)
        logger.info(f"🔍 ПАРСИНГ САЙТА: {url}")
        
        driver = None
        total_start = time.time()
        
        try:
            driver = self._create_driver()
            driver.set_page_load_timeout(self.timeout)
            
            # Переходим на страницу
            logger.info(f"  📄 Загрузка страницы...")
            page_start = time.time()
            driver.get(url)
            page_elapsed = time.time() - page_start
            logger.info(f"  ✓ Страница загружена за {page_elapsed:.2f} сек")
            
            # Ждём загрузки body
            logger.info("  ⏳ Ожидание body элемента...")
            WebDriverWait(driver, self.timeout).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            logger.info("  ✓ Body элемент найден")
            
            # Даём странице время на загрузку динамического контента
            logger.info("  ⏳ Ожидание динамического контента (2 сек)...")
            time.sleep(2)
            
            # Извлекаем title
            title = driver.title
            logger.info(f"  📌 Title: {title[:60] if title else 'N/A'}...")
            
            # Извлекаем h1
            h1 = None
            try:
                h1_element = driver.find_element(By.TAG_NAME, 'h1')
                h1 = h1_element.text.strip() if h1_element.text else None
                logger.info(f"  📌 H1: {h1[:60] if h1 else 'N/A'}...")
            except Exception as e:
                logger.debug(f"  H1 не найден: {e}")
            
            # Извлекаем первый абзац
            first_paragraph = None
            try:
                paragraphs = driver.find_elements(By.TAG_NAME, 'p')
                logger.debug(f"  Найдено абзацев: {len(paragraphs)}")
                for i, p in enumerate(paragraphs):
                    text = p.text.strip() if p.text else ""
                    if len(text) > 50:
                        first_paragraph = text[:500]
                        logger.info(f"  📌 Первый абзац (p[{i}]): {first_paragraph[:60]}...")
                        break
            except Exception as e:
                logger.debug(f"  Абзацы не найдены: {e}")
            
            # Делаем скриншот
            logger.info("  📸 Создание скриншота...")
            screenshot_start = time.time()
            screenshot_bytes = driver.get_screenshot_as_png()
            screenshot_elapsed = time.time() - screenshot_start
            screenshot_size_kb = len(screenshot_bytes) / 1024
            logger.info(f"  ✓ Скриншот создан за {screenshot_elapsed:.2f} сек ({screenshot_size_kb:.1f} KB)")
            
            total_elapsed = time.time() - total_start
            logger.info(f"  ✅ ПАРСИНГ ЗАВЕРШЁН за {total_elapsed:.2f} сек")
            logger.info("=" * 50)
            
            return title, h1, first_paragraph, screenshot_bytes, None
            
        except TimeoutException:
            total_elapsed = time.time() - total_start
            logger.error(f"  ✗ TIMEOUT за {total_elapsed:.2f} сек")
            logger.error("=" * 50)
            return None, None, None, None, "Превышено время ожидания загрузки страницы"
            
        except WebDriverException as e:
            total_elapsed = time.time() - total_start
            error_msg = str(e)
            logger.error(f"  ✗ WebDriver ошибка за {total_elapsed:.2f} сек")
            logger.error(f"  Детали: {error_msg[:200]}")
            logger.error("=" * 50)
            
            if 'net::ERR_NAME_NOT_RESOLVED' in error_msg:
                return None, None, None, None, "Не удалось найти сайт по указанному адресу"
            elif 'net::ERR_CONNECTION_REFUSED' in error_msg:
                return None, None, None, None, "Соединение отклонено сервером"
            elif 'net::ERR_CONNECTION_TIMED_OUT' in error_msg:
                return None, None, None, None, "Превышено время ожидания соединения"
            else:
                return None, None, None, None, f"Ошибка браузера: {error_msg[:200]}"
                
        except Exception as e:
            total_elapsed = time.time() - total_start
            logger.error(f"  ✗ Неизвестная ошибка за {total_elapsed:.2f} сек: {e}")
            logger.error("=" * 50)
            return None, None, None, None, f"Ошибка при загрузке страницы: {str(e)[:200]}"
            
        finally:
            if driver:
                try:
                    logger.debug("  Закрытие драйвера...")
                    driver.quit()
                    logger.debug("  ✓ Драйвер закрыт")
                except Exception as e:
                    logger.warning(f"  Ошибка при закрытии драйвера: {e}")
    
    async def parse_url(self, url: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[bytes], Optional[str]]:
        """
        Асинхронный парсинг URL через Chrome
        """
        # Добавляем протокол если его нет
        original_url = url
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
            logger.info(f"  URL дополнен протоколом: {original_url} -> {url}")
        
        logger.info(f"🚀 Запуск асинхронного парсинга: {url}")
        
        # Запускаем синхронный парсинг в отдельном потоке
        loop = asyncio.get_event_loop()
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=2)
        result = await loop.run_in_executor(
            self._executor,
            self._parse_sync,
            url
        )
        
        return result
    
    def screenshot_to_base64(self, screenshot_bytes: bytes) -> str:
        """Конвертировать скриншот в base64"""
        base64_str = base64.b64encode(screenshot_bytes).decode('utf-8')
        logger.debug(f"Скриншот конвертирован в base64: {len(base64_str)} символов")
        return base64_str
    
    async def close(self):
        """Закрыть executor"""
        logger.info("Закрытие Parser сервиса...")
        if self._executor is not None:
            executor, self._executor = self._executor, None
            await asyncio.to_thread(executor.shutdown, wait=True)
        logger.info("Parser service closed")


# Глобальный экземпляр
logger.info("Создание глобального экземпляра Parser сервиса...")
parser_service = ParserService()
