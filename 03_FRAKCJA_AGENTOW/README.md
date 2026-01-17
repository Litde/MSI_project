LightTankAgent

Jak testować lokalnie:

1. Otwórz PowerShell w katalogu `03_FRAKCJA_AGENTOW`.
2. Upewnij się, że masz Python w PATH.
3. Uruchom:

[//]: # ($env:PYTHONPATH = 'D:\Polibuda\Sezon_2_Semestr_2\MSI\MSI_project\02_FRAKCJA_SILNIKA'; python .\light_tank_agent_test.py)

Plik `light_tank_agent_test.py` doda tymczasowo silnik do sys.path, zaimportuje agenta i wywoła `get_action`.

