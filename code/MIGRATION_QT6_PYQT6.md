# Migration QGIS_FMV : PyQt5/Qt5 → PyQt6/Qt6 (Kadas)

Ce document résume ce qui a été changé pour porter le plugin de Qt5 (PyQt5,
via le shim `qgis.PyQt`) vers Qt6 (**PyQt6**, binding réellement utilisé par
Kadas), et ce qui a été vérifié par exécution réelle du code plutôt que par
simple relecture.

## 0. Important : PySide6 vs PyQt6

Une première version de cette migration ciblait **PySide6** (installé via
`pip install PySide6`), sur la base d'imports PySide6 déjà présents dans le
plugin pour `QtMultimedia`/`QtMultimediaWidgets`. **Cette approche est
incorrecte pour Kadas** et provoque un crash au chargement du plugin :

```
ImportError: DLL load failed while importing QtCore: La procédure spécifiée
est introuvable.
```

La cause : Kadas est lui-même lié à sa propre copie de Qt6 (déjà chargée en
mémoire dans le process). `pip install PySide6` télécharge un wheel qui
embarque **sa propre copie séparée** des DLL Qt6 (`Qt6Core.dll`, etc.).
Quand ces deux copies de Qt6 coexistent dans le même process Windows, le
chargeur de DLL peut résoudre les symboles sur la mauvaise version binaire
— d'où l'erreur « procédure spécifiée introuvable », signature classique
d'une incompatibilité ABI entre deux builds Qt6 différents.

**La bonne cible est PyQt6**, accédé via le shim `qgis.PyQt` (comme en Qt5) :
ce shim réutilise automatiquement le binding et les DLL déjà chargées par
Kadas, donc aucun risque de conflit. Tout le code de ce plugin utilise donc
désormais `from qgis.PyQt.QtXxx import ...`, jamais `PySide6` directement.
Si vous aviez installé `PySide6` manuellement pour faire fonctionner une
version antérieure du plugin, il n'est plus nécessaire et peut être
désinstallé.

**Différence importante entre les deux bindings** : PySide6 conserve par
compatibilité les raccourcis d'enum non qualifiés hérités de Qt5
(`Qt.LeftButton`, `QFont.Bold`, `QMediaPlayer.PlayingState`...). **PyQt6 les
a supprimés** : il faut systématiquement le chemin complet
(`Qt.MouseButton.LeftButton`, `QFont.Weight.Bold`,
`QMediaPlayer.PlaybackState.PlayingState`...). Cela concernait non
seulement le nouveau code Qt6 mais aussi une bonne partie du code Qt5
d'origine, qui utilisait ces raccourcis (valides sous PyQt5). Tout a été
qualifié explicitement — voir §1.

## 1. Changements mécaniques (imports, alias, symboles renommés)

- `PyQt5.*` → `qgis.PyQt.*` partout (imports par sous-module
  QtCore/QtGui/QtWidgets/QtNetwork/QtPrintSupport/QtMultimedia/...).
- Les imports groupés `from qgis.PyQt.Qt import ...` (module fourre-tout
  propre à PyQt) ont été éclatés en imports explicites `QtCore`/`QtGui`
  (`manager/QgsManager.py`, `videoStreaming/UDPClient.py`).
- `pyqtSignal`/`pyqtSlot` conservés tels quels (ce sont les noms PyQt
  natifs, contrairement à `Signal`/`Slot` spécifiques à PySide6).
- `.exec_()` → `.exec()` (méthode renommée, `exec_` n'existe plus en Qt6,
  quel que soit le binding).
- `QAction` a été déplacé de `QtWidgets` vers `QtGui` en Qt6 — corrigé
  partout, y compris dans les fichiers `gui/ui_*.py` compilés
  (`QtWidgets.QAction(...)` → `QtGui.QAction(...)`).
- `QVariant` n'existe plus du tout côté Python dans Qt6 (retiré de l'API
  publique au profit de `QMetaType`) :
  - `QVariant.String/Double/Int/Bool` → `QMetaType.Type.QString/Double/Int/Bool`
    (`utils/QgsFmvLayers.py`, `utils/KadasFmvLayers.py`, pour construire les
    `QgsField`).
  - `QVariant()` (valeur nulle renvoyée par un modèle Qt) → `None`
    (`utils/QgsJsonModel.py`).
- `QRegExp`/`QRegExpValidator` (supprimés) → `QRegularExpression`/
  `QRegularExpressionValidator` (`manager/QgsFmvOpenStream.py`).
- `QApplication.desktop()` (supprimé) → `self.screen()` (`video/QgsVideo.py`,
  `player/QgsFmvPlayer.py`).
- `QPalette.Background` (renommé) → `QPalette.ColorRole.Window`.
- `QPainter.HighQualityAntialiasing` (supprimé) → `QPainter.RenderHint.Antialiasing`.
- `QPrinter.A4` / `QPrinter.paperSize(unit)` (retirés de `QPrinter` en Qt6) →
  `QPrinter.setPageSize(QPageSize(QPageSize.PageSizeId.A4))` et
  `printer.pageLayout().pageSize().size(QPageSize.Unit.Point)`
  (`player/QgsFmvMetadata.py`).
- `QDockWidget.AllDockWidgetFeatures` (supprimé de Qt6) → combinaison
  explicite `DockWidgetClosable | DockWidgetMovable | DockWidgetFloatable`.
- `QRubberBand.Rectangle` → `QRubberBand.Shape.Rectangle`.
- `media.canonicalUrl()` (reliquat de l'API `QMediaContent` supprimée,
  `media` est maintenant un `QUrl` brut) → `media.toString()`
  (`player/QgsFmvPlayer.py`).
- `QLayout.setMargin(0)` (supprimé de Qt6) →
  `setContentsMargins(0, 0, 0, 0)` (`gui/ui_FmvManager.py`).

**Tous les enums Qt du code métier et des fichiers UI compilés ont été
qualifiés explicitement**, y compris les accès **via une instance** plutôt
que via la classe (ex. `self.style.ComplexControl.CC_Slider`, pas seulement
`QStyle.ComplexControl.CC_Slider`). Liste non exhaustive des enums
concernés : `Qt.AlignmentFlag.*`, `Qt.MouseButton.*`, `Qt.CursorShape.*`,
`Qt.Key.*`, `Qt.WindowType.*`, `Qt.GlobalColor.*`, `Qt.DockWidgetArea.*`,
`Qt.ContextMenuPolicy.*`, `Qt.FocusPolicy.*`, `Qt.ScrollBarPolicy.*`,
`Qt.ToolBarArea.*`, `QFont.Weight.*`, `QFont.StyleStrategy.*`,
`QStyle.ComplexControl.*`/`SubControl.*`, `QMediaPlayer.MediaStatus.*`/
`PlaybackState.*`, `QMessageBox.StandardButton.*`, `QTextCursor.MoveOperation.*`,
`QTextFormat.PageBreakFlag.*`, `QSizePolicy.Policy.*`, `QHeaderView.ResizeMode.*`,
`QImage.Format.*`, `QLineEdit.EchoMode.*`, `QFileDialog.Option.*`,
`QAbstractItemView.DragDropMode/SelectionMode/SelectionBehavior/EditTrigger`,
`QAbstractScrollArea.SizeAdjustPolicy`, `QComboBox.SizeAdjustPolicy`,
`QFormLayout.FieldGrowthPolicy/ItemRole`, `QFrame.Shape/Shadow`,
`QIcon.Mode/State`, `QLocale.Language/Country`, `QToolButton.ToolButtonPopupMode`,
`QAction.Priority`.

Les enums propres à l'API QGIS elle-même (`Qgis.Warning/Info/Critical`,
`QgsTask.CanCancel`, `QgsWkbTypes.PointGeometry`, etc.) n'ont **pas** été
touchés : ce sont des enums sip/QGIS, pas des enums Qt, et leur convention
d'accès ne dépend pas du binding Qt utilisé.

## 2. Le gros morceau : le pipeline vidéo (`video/QgsVideo.py`)

Qt6 a supprimé **`QAbstractVideoSurface`** et **`QAbstractVideoBuffer`**
(remplacés par `QVideoSink` + `QVideoFrame`). La classe `VideoWidgetSurface`
a été entièrement réécrite sur la nouvelle API :

- Elle hérite maintenant de `QVideoSink` au lieu de `QAbstractVideoSurface`.
- Elle se connecte au signal `videoFrameChanged` pour récupérer les images.
- La conversion pixel-par-pixel manuelle (`QImage(frame.bits(), ...)`) a été
  remplacée par `QVideoFrame.toImage()`, méthode ajoutée nativement en Qt6.
- `VideoWidget` n'hérite plus de `QVideoWidget` mais directement de
  `QWidget` : son `paintEvent` était de toute façon entièrement custom.
- Toute la logique de dessin par-dessus la vidéo (rubberbands, GCP, filtres
  image, magnifier, censure, tracking d'objet) a été conservée telle quelle,
  la nouvelle classe exposant la même API (`videoRect()`/`sourceRect()`/
  `paint(painter)`).

**Testé par exécution réelle** (§6) : `VideoWidget` instancié, redimensionné
et affiché ; `VideoWidgetSurface` confirmée comme sous-classe fonctionnelle
de `QVideoSink`. Reste à valider avec un vrai flux vidéo dans Kadas (§7).

## 3. La playlist (`QMediaPlaylist` a disparu de Qt6)

Classe de remplacement `utils/QgsMediaPlaylist.py` + câblage explicite dans
`QgsFmvPlayer.attachPlaylist()` (chargement de source, avance automatique
en fin de vidéo, mode boucle/séquentiel).

**Testé par exécution réelle** : chargement de source depuis la playlist
confirmé, avance en boucle vérifiée pas à pas (dernier élément → retour au
premier), gestion `EndOfMedia` déclenchée manuellement et confirmée.

## 4. `QMediaPlayer` : volume, mute, disponibilité vidéo

Volume/mute portés par un nouvel objet `QAudioOutput` séparé du lecteur
(`player.setVolume(v)`/`isMuted()` n'existent plus sur `QMediaPlayer`).

**Testé par exécution réelle** : `setVolume(50)` → `audioOutput.volume() ==
0.5`, `setMuted()` bascule correctement dans les deux sens.

## 5. Fichiers UI et ressources : ne pas régénérer depuis les `.ui`

**Point important** : les fichiers `gui/ui_*.py` de ce paquet **ne sont pas
régénérés depuis les fichiers `.ui`** (contrairement à une première version
de cette migration). Ce sont les fichiers PyQt5 **originaux déjà compilés**,
fournis dans le zip de départ et prouvés fonctionnels, auxquels les mêmes
transformations de migration ont été appliquées directement (imports
`qgis.PyQt`, enums qualifiés, `QAction` déplacé vers `QtGui`, `setMargin()`
→ `setContentsMargins()`).

**Pourquoi ne pas régénérer depuis les `.ui`** : au moins deux fichiers
`.ui` fournis dans ce dépôt ont dérivé de la structure réellement utilisée
par le code Python, et régénérer aveuglément aurait cassé le plugin :

- `ui/ui_FmvPlayer.ui` ne déclare pas la promotion des widgets
  `sliderDuration`/`volumeSlider` vers la classe custom `QgsFmvSlider` (ils
  y apparaissent comme de simples `QSlider`), alors que le code appelle
  `sliderDuration.mousePressed`, un signal qui n'existe que sur
  `QgsFmvSlider`.
- `ui/ui_FmvManager.ui` déclare carrément un widget racine de type
  **`QDockWidget`** (avec `setFeatures()`/`setWidget()`), alors que la
  classe applicative réelle, `FmvManager(QWidget, Ui_ManagerWindow)`, est
  un simple `QWidget` avec un layout plat. Une régénération naïve provoque
  un crash immédiat à l'ouverture du plugin
  (`AttributeError: 'FmvManager' object has no attribute 'setFeatures'`).
- `ui/ui_FmvOptions.ui` référence le widget `QgsColorButton` via son header
  C++ (`qgscolorbutton.h`), que les générateurs standards (`pyuic6`)
  traduisent naïvement en `import qgscolorbutton` (module Python
  inexistant). Le vrai widget est fourni par QGIS lui-même : le bon import
  est `from qgis.gui import QgsColorButton`.

**Si vous modifiez un jour un `.ui` dans Qt Designer et le régénérez**, il
faudra vérifier/rétablir manuellement ces trois points.

`gui/resources_rc.py` a été régénéré avec `pyside6-rcc` (aucun `pyrcc6`
n'est fourni par le paquet PyQt6 moderne), puis sa seule ligne de binding
(`from PySide6 import QtCore`) corrigée en `from qgis.PyQt import QtCore`.
Le format binaire des ressources est produit par l'outil `rcc` de Qt
lui-même et est indépendant du binding Python — seule la ligne d'import du
glue code compte. Vérifié par chargement réel d'une icône embarquée sous
PyQt6 (`QIcon(':/imgFMV/images/icon.png').isNull()` → `False`).

## 6. Méthodologie de vérification (exécution réelle, pas juste relecture)

Après le premier échec en conditions réelles (crash DLL PySide6, puis le
crash `setFeatures` révélant la désynchronisation `.ui`/`.py`), la suite de
cette migration a été vérifiée avec un vrai interpréteur PyQt6 (6.11)
plutôt que par relecture statique seule :

- Un faux paquet `qgis`/`kadas` minimal (stubs) a été construit pour
  **importer et instancier** chaque module du plugin hors de Kadas.
- `setupUi()` de chaque fenêtre a été exécuté sur un vrai widget Qt (mode
  `offscreen`), révélant les enums mal qualifiés, le bug
  `AllDockWidgetFeatures`, et `QtWidgets.QAction`.
- `video.QgsVideo.VideoWidget` a été instancié, redimensionné et affiché
  réellement.
- `player.QgsFmvPlayer.QgsFmvPlayer`, `manager.QgsManager.FmvManager`,
  `player.QgsFmvMetadata.QgsFmvMetadata`, `manager.QgsFmvOpenStream.OpenStream`
  et `manager.QgsMultiplexor.Multiplexor` ont tous été **instanciés avec
  leurs vraies classes** (pas juste des `QWidget`/`QDialog` nus), et pour
  le lecteur, tout le cycle playlist → source → volume → mute →
  fin-de-média a été exercé pas à pas.

Cette méthode a permis de trouver et corriger des bugs réels qu'une simple
relecture n'aurait probablement pas détectés : le décalage `.ui`/`.py`
(§5), un enum accédé via une instance de `QStyle` plutôt que sa classe, le
reliquat `canonicalUrl()`, et `QtWidgets.QAction` resté non corrigé dans
les fichiers UI compilés.

**Limites de cette vérification** : toujours pas de test avec un vrai
Kadas, un vrai flux vidéo (fichier MISB ou flux RTP/UDP), ni de rendu GPU
réel (tout a tourné en mode `offscreen`). Les stubs `qgis.core`/`qgis.gui`/
`kadas.kadasgui` utilisés renvoient des objets factices : toute méthode
QGIS/Kadas non-Qt (ex. `QgsProject`, `QgsTask`, `KadasPluginInterface`) n'a
donc **pas** été testée dans son vrai comportement, seulement vérifiée pour
ne pas planter à l'import/l'instanciation.

## 7. Ce qui reste à vérifier dans Kadas

1. Lecture effective d'une vidéo locale (MP4/TS) et d'un flux réseau
   (RTP/UDP), rendu de l'image (filtres, magnifier, dessin par-dessus la
   vidéo) — le pipeline `QVideoSink`/`toImage()` n'a été testé qu'avec des
   frames vides ici.
2. `videoStreaming/UDPClient.py` — imports migrés et import vérifié, mais
   pas audité ligne à ligne avec le même niveau de détail que le lecteur
   principal, ni instancié.
3. Export PDF des métadonnées (`player/QgsFmvMetadata.py`).
4. `video/QgsColor.py` (dialogue de couleurs) — import et enums vérifiés,
   mais pas d'instanciation complète possible ici (nécessite un vrai
   `VideoWidget` avec une vidéo chargée pour lire `brightness()` etc.).
5. Tous les raccourcis d'action liés au dessin (points/lignes/polygones,
   mesure, censure, tracking d'objet) — dépendent du pipeline vidéo réécrit
   mais leur logique elle-même est inchangée.
6. Si `ui_FmvPlayer.ui`, `ui_FmvManager.ui` ou `ui_FmvOptions.ui` sont un
   jour régénérés depuis Qt Designer, restaurer les trois points du §5.

## 9. Incident post-livraison n°2 : `QEvent`/`QMouseEvent` non qualifiés et API souris changée en Qt6

Une fois `FmvManager` corrigé (§9 précédent), un nouveau crash est apparu à
l'usage réel dans Kadas :

```
AttributeError: type object 'QEvent' has no attribute 'MouseButtonPress'
```

Deux catégories de bugs liés à `QEvent`/`QMouseEvent`, non couvertes par
les scripts de qualification d'enums précédents (`QEvent` n'était pas dans
leur liste), ont été trouvées et corrigées :

**a) Enums `QEvent` non qualifiés** : `QEvent.MouseButtonPress` →
`QEvent.Type.MouseButtonPress` (`manager/QgsManager.py`), `QEvent.MouseMove`
→ `QEvent.Type.MouseMove` (`video/QgsVideo.py`).

**b) API souris changée en Qt6** — plus large et plus insidieuse :
`QMouseEvent` a perdu ses méthodes `.x()`, `.y()` et `.pos()` (qui
renvoyaient des entiers/`QPoint`), remplacées par `.position()` (qui
renvoie un `QPointF`). Ce changement touchait :

- `video/QgsVideo.py` : construction manuelle d'un `QMouseEvent` avec
  `QPoint` au lieu de `QPointF` (le constructeur Qt6 exige `QPointF`), et
  une dizaine d'appels `event.x()`/`event.y()`/`event.pos()` dans les
  handlers `mouseMoveEvent`/`mousePressEvent`/`mouseDoubleClickEvent` —
  tous remplacés par `event.position().x()`/`.y()` ou
  `event.position().toPoint()` selon que le code en aval attend un flottant
  ou un `QPoint` (ex. pour construire un `QRect`).
- `video/QgsVideoUtils.py` (`GetTransf`/`GetAffineTransf`) : ces fonctions
  reçoivent **soit** un vrai `QMouseEvent`, **soit** un `QPoint` brut
  construit ailleurs dans le code (`QPoint(xc, yc)`), donc un simple
  remplacement uniforme aurait cassé l'un des deux cas. Une petite méthode
  utilitaire `VideoUtils._eventXY(event)` a été ajoutée : elle utilise
  `.position()` si disponible (vrai `QMouseEvent`), sinon `.x()`/`.y()`
  directement (objet `QPoint`).
- `manager/QgsManager.py` (`eventFilter`) : `event.pos()` →
  `event.position().toPoint()` (l'événement reçu est en réalité un
  `QMouseEvent` malgré la signature générique `QEvent` de `eventFilter` —
  comportement Qt normal, `itemAt()` attend un `QPoint`).
- `utils/QgsFmvSlider.py` (`mousePressEvent`) : même correction.

**Vérifié par exécution réelle** avec de vrais `QMouseEvent` construits
correctement (`QPointF`, pas `QPoint`) : `QgsManager.eventFilter()`,
`VideoWidget.mouseMoveEvent()`, `.mousePressEvent()`,
`.mouseDoubleClickEvent()`, et `QgsFmvSlider.mousePressEvent()` ont tous
été appelés directement et confirmés fonctionnels.

D'autres petits enums non qualifiés, ratés par les scripts précédents,
ont été corrigés au passage : `QTextCharFormat.AlignMiddle` →
`QTextCharFormat.VerticalAlignment.AlignMiddle`, `QPrinter.HighResolution`
→ `QPrinter.PrinterMode.HighResolution`, `QPrinter.PdfFormat` →
`QPrinter.OutputFormat.PdfFormat` (tous dans `player/QgsFmvMetadata.py`).

**Recommandation pour la suite** : ces deux incidents montrent que même
avec un harnais de test exécutant `setupUi()` et instanciant les classes,
certains chemins de code (comme les handlers d'événements souris, jamais
déclenchés par une simple instanciation) ne sont exercés qu'à l'usage réel
dans Kadas. Si d'autres `AttributeError` du même type apparaissent
(méthode manquante sur un objet Qt), il s'agit très probablement soit d'un
enum non qualifié, soit d'une méthode renommée/déplacée entre Qt5 et Qt6 —
la stack trace donnera la classe et l'attribut en cause, qu'on peut
vérifier directement avec `python3 -c "from PyQt6.QtXxx import Classe;
print(dir(Classe))"`.

## 11. Incident post-livraison n°3 : reliquat `surfaceFormat()` sur `VideoWidgetSurface`

```
AttributeError: 'VideoWidgetSurface' object has no attribute 'surfaceFormat'
```

`VideoWidget.sizeHint()` (méthode `QWidget` standard, utilisée par le
système de layout Qt) appelait encore `self.surface.surfaceFormat().sizeHint()`
— un reliquat de l'ancienne API `QAbstractVideoSurface` (qui exposait
`surfaceFormat()`) resté dans la réécriture du §2, alors que la nouvelle
`VideoWidgetSurface` (basée sur `QVideoSink`) expose désormais directement
sa propre méthode `sizeHint()`. Corrigé en `self.surface.sizeHint()`
(`video/QgsVideo.py`).

Ce site n'avait pas été couvert par les tests précédents car `sizeHint()`
n'est appelé par Qt que lors du calcul de layout, jamais lors d'une simple
instanciation. Un test dédié l'appelle désormais explicitement. Une
vérification croisée de tous les appels `surface.xxx()` dans l'ensemble du
plugin contre l'API réellement exposée par `VideoWidgetSurface`
(`isActive`, `paint`, `sizeHint`, `sourceRect`, `updateVideoRect`,
`videoRect`) n'a révélé aucun autre reliquat.

## 13. Incident post-livraison n°4 : boucle infinie playlist ↔ manager (`RecursionError`)

```
RecursionError: maximum recursion depth exceeded
```

Bug introduit par ma propre classe de remplacement de `QMediaPlaylist`
(§3). `QgsMediaPlaylist.setCurrentIndex()` émettait **inconditionnellement**
`currentIndexChanged`/`currentMediaChanged`, même en resélectionnant
l'index déjà courant. Or le flux normal du plugin crée une boucle de
rétroaction entre le gestionnaire et le lecteur :

```
QgsManager.SetupPlayer(row)
  → playlist.setCurrentIndex(row)
    → émet currentMediaChanged
      → QgsFmvPlayer._onPlaylistMediaChanged()
        → player.currentMediaChanged(media)
          → self.parent.SetupPlayer(idx)   # idx == row : on revient au point de départ
```

Le vrai `QMediaPlaylist` de Qt5 n'émettait pas ses signaux quand on lui
redemandait le même index (convention Qt standard : un setter ne réémet
pas si la valeur ne change pas), donc cette boucle n'existait pas dans le
code d'origine. **Corrigé** en ajoutant la même garde dans
`utils/QgsMediaPlaylist.py` : `setCurrentIndex()` retourne immédiatement,
sans rien émettre, si l'index demandé est déjà l'index courant.

**Vérifié par exécution réelle** : un test dédié reproduit exactement le
scénario du crash (un `SetupPlayer(row)` factice qui rappelle
`playlist.setCurrentIndex(row)`, comme le vrai code) et confirme que
l'appel se stabilise après le rebond attendu (2 appels, pas une explosion)
au lieu de récurser indéfiniment, et qu'une sélection sur une ligne
réellement différente continue de fonctionner normalement.

## 14. Incident post-livraison n°5 : `QgsPlayerTest.py`, module `QtMultimediaWidgets` absent du shim Kadas

```
ModuleNotFoundError: No module named 'qgis.PyQt.QtMultimediaWidgets'
```

`player/QgsPlayerTest.py` est un script de démonstration standalone
(tutoriel PySide/PyQt générique, sans rapport fonctionnel avec QGIS_FMV) —
utile pour tester le backend vidéo Qt seul, sans passer par toutes les
couches du plugin. Il n'est **jamais importé par aucun autre fichier du
plugin** (vérifié par recherche exhaustive), mais Kadas tente néanmoins de
le charger (très probablement un mécanisme de scan/rechargement de plugin
qui importe tous les `.py` du dossier), et plantait car le shim
`qgis.PyQt` de ce build de Kadas n'expose pas le sous-module
`QtMultimediaWidgets` — contrairement à `QtMultimedia`, qui lui fonctionne
correctement (aucun crash rapporté sur le vrai lecteur vidéo, qui utilise
`QtMultimedia` sans passer par `QtMultimediaWidgets` depuis la réécriture
du §2).

**Corrigé avec un import de repli** plutôt qu'en supprimant le fichier :

```python
try:
    from qgis.PyQt.QtMultimediaWidgets import QVideoWidget
except ImportError:
    from PyQt6.QtMultimediaWidgets import QVideoWidget
```

Importer directement `PyQt6.QtMultimediaWidgets` en repli est sûr ici :
contrairement au `pip install PySide6` initial (§0), qui embarquait sa
**propre copie séparée** des DLL Qt6 et provoquait un conflit ABI, ceci
importe le **même** PyQt6 déjà chargé par Kadas — aucune DLL
supplémentaire, aucun risque de conflit.

**Vérifié par exécution réelle** dans les deux cas de figure : import
normal (shim disponible, comme dans cet environnement de test) et import
avec le repli forcé (en supprimant artificiellement
`qgis.PyQt.QtMultimediaWidgets` du `sys.modules` avant l'import, pour
simuler le comportement réel du build Kadas signalé). `VideoWindow` a été
instancié avec succès dans les deux cas.

## 16. Incident post-livraison n°6 : écran noir en rouvrant une vidéo déjà lue

Avant ce sixième incident, `player.setVideoOutput(sink)` a été remplacé par
`player.setVideoSink(sink)` dans `player/QgsFmvPlayer.py` — Qt6 expose les
deux méthodes sur `QMediaPlayer`, mais `setVideoSink(sink: QVideoSink)` est
la méthode **typée précisément** pour attacher un `QVideoSink` brut (notre
cas, `VideoWidgetSurface` étant une sous-classe Python de `QVideoSink`),
alors que `setVideoOutput(a0: QObject)` est une méthode générique qui
détecte le type de l'objet passé au niveau C++. Ce changement reste en
place car c'est l'API la plus correcte pour ce cas d'usage, mais **son
lien avec le bug d'écran noir n'a pas pu être confirmé** : un test avec une
vraie vidéo décodée (générée via `ffmpeg`) a montré que le pipeline
fonctionnait de bout en bout aussi bien avec l'ancienne méthode
(`setVideoOutput`) qu'avec la nouvelle (`setVideoSink`), dans cet
environnement Linux/FFmpeg. Le vrai bug d'écran noir rencontré ensuite
(§16 ci-dessous) avait en réalité une tout autre cause.

## 17. Incident post-livraison n°6 : écran noir en rouvrant une vidéo déjà lue

Après le passage à `setVideoSink()` (§ session précédente), un nouveau
symptôme est apparu : la vidéo fonctionnait à la première ouverture, mais
en la fermant puis en la rouvrant, l'écran restait noir, sans le moindre
log de lecture des métadonnées (aucun `spawned : ffmpeg...`). Diagnostic
confirmé par l'utilisateur dans la console Kadas :

```
hasVideo: False
mediaStatus: MediaStatus.NoMedia
videoSink: None
```

**Root cause** : régression directe du correctif de la boucle infinie du
§13. `QgsManager` **ne recrée jamais le lecteur** entre deux ouvertures —
`self._PlayerDlg` n'est jamais remis à `None` après `.close()`, donc
`CreatePlayer()` (et `attachPlaylist()`) n'est appelé qu'une seule fois
par session. Toutes les ouvertures suivantes passent uniquement par
`SetupPlayer(row)` → `playlist.setCurrentIndex(row)`. Or la garde ajoutée
au §13 (`if index == self._currentIndex: return`) bloquait **aussi** ce
cas légitime : rouvrir la **même** vidéo (même `row`) ne redéclenchait
jamais `currentMediaChanged`, donc `player.setSource()` n'était jamais
rappelé — le lecteur restait sur `NoMedia`.

**Corrigé en déplaçant la protection au bon endroit** :
- `utils/QgsMediaPlaylist.py` : `setCurrentIndex()` réémet à nouveau
  systématiquement ses signaux (retour au comportement d'origine — un
  index redemandé doit toujours notifier ses abonnés, c'est le contrat
  normal d'une playlist).
- `manager/QgsManager.py` (`SetupPlayer`) : garde de **réentrance** ciblée
  à la place, avec un simple booléen (`self._settingUpPlayer`). Elle casse
  précisément le cycle `SetupPlayer → setCurrentIndex → currentMediaChanged
  → QgsFmvPlayer.currentMediaChanged → SetupPlayer` (l'appel ré-entrant
  n'apporte aucune information nouvelle et doit être ignoré), sans jamais
  empêcher un appel *initial* et légitime de `SetupPlayer(row)` — y compris
  quand `row` est identique à la dernière sélection.

**Vérifié par exécution réelle**, sur les deux scénarios simultanément :
1. Reproduction exacte du scénario rapporté (ouverture d'une vidéo,
   fermeture, réouverture de la **même** ligne) : `player.setSource()` est
   bien rappelé à chaque réouverture (4 appels sur 4 ouvertures dans le
   test, y compris deux resélections consécutives de la même ligne).
2. Le scénario original de `RecursionError` (§13) reste corrigé : aucune
   explosion d'appels, `SetupPlayer` se stabilise après le rebond attendu.

Ce test dédié (`test_reopen_same_video.py` dans la session de travail)
reproduit fidèlement le vrai flux de `QgsManager` (un seul lecteur créé,
réutilisé indéfiniment, `SetupPlayer` toujours appelé) plutôt qu'un
scénario simplifié — c'est ce niveau de fidélité qui a permis de détecter
cette régression avant qu'elle ne reparte en production une seconde fois.

## 19. Incident post-livraison n°7 : écran noir à l'ouverture (mais pas en rouvrant)

Différent du §17 : cette fois, l'écran restait noir **dès la première
ouverture** d'une vidéo depuis le manager, mais s'affichait correctement
en fermant puis rouvrant la même fenêtre.

**Diagnostic** : `manager/QgsManager.CreatePlayer()` appelle
`self._PlayerDlg.setWindowFlags(...)` juste avant `.show()` — or changer
les `windowFlags()` d'un widget déjà construit **force Qt à recréer sa
fenêtre native** (comportement Qt documenté, identique en Qt5, donc pas
introduit par la migration). Juste après, `SetupPlayer()` démarre la
lecture, et les toutes premières frames peuvent arriver **avant que la
fenêtre fraîchement recréée soit pleinement exposée** par le système de
fenêtrage. `VideoWidgetSurface.present()` appelle `self.widget.update()`
à chaque frame, mais `update()` ne fait que *planifier* un repaint pour la
prochaine itération de la boucle d'événements — et Qt peut silencieusement
laisser tomber cette planification si le widget n'est pas encore exposé.
Fermer/rouvrir déclenche un cycle complet d'exposition qui rattrape
l'affichage, d'où le symptôme.

**Premier correctif tenté (abandonné) : `repaint()` synchrone.** L'idée de
forcer un repaint immédiat et synchrone sur la toute première frame après
activation semblait logique, mais **a provoqué un `Segmentation fault`**
lors du test avec une vraie vidéo décodée. Appeler `repaint()` (peinture
immédiate, réentrante) depuis l'intérieur même du callback
`videoFrameChanged` est un anti-pattern Qt connu, et s'est révélé
incompatible avec le backend logiciel de conversion CPU utilisé par ce Qt
Multimedia (repéré dans les logs : *"No RHI backend. Using CPU
conversion"*). Confirmé que `present()` s'exécute bien sur le thread GUI
(donc ce n'était pas un problème de thread), mais la réentrance elle-même
du repaint synchrone était la cause du crash.

**Deuxième piste, également écartée : lire l'ancienne frame avant
remplacement.** Une version intermédiaire calculait
`was_inactive = not self._currentFrame.isValid()` (juste une lecture,
avant d'écraser `self._currentFrame` par la nouvelle frame) — même ce
simple test de validité sur l'**ancienne** frame, juste avant son
remplacement, suffisait à faire planter ce backend. Isolé par test
incrémental (bissection : d'abord la version de base sans rien, qui
fonctionne ; puis l'ajout de cette seule ligne, qui plante immédiatement).
Cela suggère une fragilité du cycle de vie des objets `QVideoFrame` de ce
backend logiciel autour du moment précis de la substitution d'une frame
par la suivante.

**Correctif final, sûr** : au lieu de calculer l'état à partir de
l'ancienne frame, un simple booléen Python (`self._hasPresentedFirstFrame`,
jamais lié à un objet Qt) suit si la toute première frame valide a déjà
été présentée. Sur cette toute première frame, un **second `update()`
différé** est programmé via `QTimer.singleShot(50, self.widget.update)` —
toujours asynchrone (passe par la boucle d'événements normale, donc sûr),
mais donne une seconde chance de rattraper l'affichage 50ms plus tard,
quand la fenêtre est certainement exposée :

```python
def present(self, frame):
    ''' Present Frame (connected to videoFrameChanged) '''
    self._currentFrame = frame
    if frame.isValid():
        self._sourceRect = frame.surfaceFormat().viewport()
        self.updateVideoRect()
    self.widget.update()
    if frame.isValid() and not self._hasPresentedFirstFrame:
        self._hasPresentedFirstFrame = True
        QTimer.singleShot(50, self.widget.update)
    return True
```

**Vérifié par exécution réelle**, avec une vraie vidéo décodée
(`ffmpeg`-générée) : aucun crash, `paintEvent()` s'exécute normalement
(34 appels sur 2 secondes de lecture), `_hasPresentedFirstFrame` bascule
correctement à `True` après la première frame.

**Leçon retenue pour la suite** : ce backend Qt Multimedia (rendu logiciel
CPU, sans RHI) semble fragile dès qu'on touche un objet `QVideoFrame` en
dehors du chemin d'usage strictement prévu (lecture de la *nouvelle* frame
uniquement, jamais de l'ancienne au moment de la transition). Toute
modification future de `VideoWidgetSurface.present()`/`paint()` devrait
éviter de conserver ou d'interroger une référence à une frame après
qu'elle a été remplacée, et éviter tout appel à `repaint()` (synchrone)
depuis un callback connecté à `videoFrameChanged` — `update()` uniquement,
éventuellement différé via `QTimer.singleShot`.

## 20. Fichier ajouté

- `utils/QgsMediaPlaylist.py` (nouveau) : implémentation de remplacement de
  `QMediaPlaylist`, voir §3.
