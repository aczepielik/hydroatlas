# Rozszerzona lista wskaźników i wizualizacji hydrologicznych

Poniższa lista zawiera propozycje rozszerzenia prezentacji danych o stacji rzecznej, skupiając się na aspekcie ekologicznym i statystycznym.

| Nazwa | Definicja | Powód umieszczenia | Potencjalne trudności |
| :--- | :--- | :--- | :--- |
| **IHA (Indicators of Hydrologic Alteration)** | Grupa 33 parametrów opisujących wielkość, czas trwania, moment wystąpienia, częstotliwość i tempo zmian przepływów. | Standard w ekohydrologii; pozwala ocenić stopień antropogenicznego zaburzenia reżimu. | Wymaga długich, nieprzerwanych ciągów danych do wiarygodnej analizy trendów. |
| **BFI (Baseflow Index)** | Stosunek odpływu podziemnego (podstawowego) do odpływu całkowitego. | Kluczowy dla zrozumienia retencji zlewni i odporności na suszę. | Algorytmy separacji hydrogramu są subiektywne (różne wyniki dla różnych filtrów). |
| **Wskaźnik Flashiness (Richards-Baker)** | Suma bezwzględnych zmian przepływu dobowego podzielona przez sumę przepływów. | Pozwala ocenić "gwałtowność" rzeki i wpływ uszczelnienia zlewni (urbanizacji). | Bardzo wrażliwy na błędy pomiarowe pojedynczych dni. |
| **Krzywa Czasu Trwania Przepływów (FDC)** | Wykres prawdopodobieństwa przewyższenia danego przepływu. | Podstawa projektowania hydrotechnicznego i wyznaczania przepływów nienaruszalnych. | Trudna w interpretacji dla laików bez naniesionych stref (np. "suche", "wilgotne"). |
| **Hydrogram Rastrowy (2D)** | Reprezentacja przepływu, gdzie osie to dni roku i lata, a kolor to intensywność. | Pozwala natychmiastowo dostrzec wieloletnie zmiany w terminach wezbrań i niżówek. | Wymaga wysokiej rozdzielczości wizualnej i dobrego doboru skali barwnej. |
| **Współczynniki Pardégo (miesięczne)** | Stosunek średniego przepływu miesięcznego do średniego przepływu rocznego. | Klasyczna miara sezonowości reżimu; łatwo porównywalna między rzekami. | Nie uwzględnia wewnątrz-miesięcznej zmienności. |
| **Wskaźniki Colwella (P, C, M)** | Miary przewidywalności, stałości i sezonowości zjawisk. | Informuje, jak stabilne jest środowisko życia organizmów wodnych. | Wymaga kategoryzacji danych ciągłych (dyskretyzacji). |
| **E-flow (Przepływ Środowiskowy)** | Minimalny przepływ wymagany do zachowania ekosystemu. | Najważniejszy wskaźnik z punktu widzenia ochrony przyrody. | Brak jednej, uniwersalnej metody wyznaczania (metody hydrauliczne vs biologiczne). |
| **Analiza Recesji (Krzywa Master)** | Wykres spadku przepływu w okresach bezopadowych. | Pozwala estymować zasoby wód podziemnych zasilających rzekę. | Wymaga precyzyjnego wyodrębnienia czystych okresów bezopadowych. |
| **Analiza Deficytu Odpływu (Threshold Level Method)** | Sumaryczna objętość wody, której zabrakło do osiągnięcia progu niżówki. | Lepsza miara dotkliwości suszy niż sam czas trwania. | Wybór progu (np. Q90 czy Q95) znacząco zmienia wyniki. |
| **Zmienność Bezwzględna (CV)** | Współczynnik zmienności przepływów dobowych/rocznych. | Prosta miara stabilności reżimu. | Może być mylący w rzekach o naturalnie wysokiej dynamice (górskich). |
| **Spektrogram Przepływów** | Analiza częstotliwościowa (np. transformata falkowa) szeregu czasowego. | Ujawnia cykle (np. wieloletnie cykle klimatyczne NAO). | Bardzo wysoki poziom skomplikowania interpretacji. |
| **Q/P (Wskaźnik Odpływu)** | Stosunek sumy odpływu do sumy opadu w zlewni. | Pokazuje efektywność zlewni i straty na parowanie. | Wymaga dostępu do danych opadowych o gęstej sieci. |
| **Dni powyżej progu (High Flow Pulses)** | Liczba dni w roku z przepływem przekraczającym np. Q10. | Istotne dla geomorfologii koryta i ekologii terenów zalewowych. | Wymaga precyzyjnej definicji "impulsu" (odstęp między zdarzeniami). |
| **SSFI (Standardized Streamflow Index)** | Standaryzowany wskaźnik odpływu (analogicznie do opadowego SPI). | Pozwala na statystyczne porównywanie susz hydrologicznych w różnych regionach. | Wymaga dopasowania odpowiedniego rozkładu statystycznego (np. Gamma, Log-Logic). |
| **Timing of Extremes** | Data (dzień juliański) wystąpienia rocznego maksimum i minimum. | Kluczowy dla ekologii (np. tarło ryb) i analizy zmian klimatu. | Duża zmienność między latami utrudnia wyznaczenie trendu. |
| **Rise/Fall Rates** | Średnie tempo narastania i opadania wezbrań (m³/s na dzień). | Miara wpływu antropogenicznego (np. praca elektrowni szczytowo-pompowej). | Wymaga danych o wysokiej rozdzielczości czasowej. |
| **Liczba dni bezodpływowych** | Suma dni w roku z przepływem Q = 0. | Krytyczne dla rzek okresowych i oceny ekstremalnego wysychania. | Często mylone z błędami pomiarowymi (zamarznięcie koryta, zator). |
| **Typologia lat hydrologicznych** | Klasyfikacja lat (suche, normalne, mokre) na podstawie kwantyli odpływu rocznego. | Pozwala na kontekstową analizę bieżących zjawisk na tle historycznym. | Arbitralność progów podziału (np. 15/70/15%). |
| **Autokorelacja przepływów dobowych** | Współczynnik korelacji szeregu z samym sobą z opóźnieniem (lag-1). | Miara "pamięci" zlewni i jej bezwładności hydrologicznej. | Silnie zależna od wielkości zlewni (skalowanie). |
