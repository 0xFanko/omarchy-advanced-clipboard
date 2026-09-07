# Omarchy Clipboard

A plugin for [Omarchy](https://omarchy.org/) that improves the clipboard history experience.

It makes it easy to find and reuse previously copied text, links, files, and images directly from the Omarchy interface.

## Features

- search through clipboard history;
- preview text, files, and images;
- detect copied links and show a static preview of the page's first viewport;
- display the source application;
- show detailed information such as type, date, time, URL, and title;
- edit text entries directly from the clipboard history;
- delete one entry or clear the entire history;
- customize every internal keyboard shortcut;
- exclude selected applications from clipboard history.

## Configuration

Les réglages utilisateur sont lus depuis la racine du plugin :

```text
<racine-du-plugin>/clipboard.json
```

Depuis cette racine, copiez le fichier d’exemple puis adaptez-le :

```bash
cp clipboard.example.json clipboard.json
```

`clipboard.json` est ignoré par Git afin que les réglages personnels ne bloquent pas les mises à jour du plugin.

```json
{
  "shortcuts": {
    "close": "Escape",
    "previousEntry": "Up",
    "nextEntry": "Down",
    "previousPage": "PgUp",
    "nextPage": "PgDown",
    "firstEntry": "Home",
    "lastEntry": "End",
    "pasteEntry": "Return",
    "copyEntry": "Shift+Return",
    "openEntry": "Alt+Return",
    "editEntry": "Ctrl+E",
    "saveEdit": "Ctrl+S",
    "deleteEntry": "Ctrl+X",
    "clearHistory": "Ctrl+Shift+X"
  },
  "historyRetentionDays": 30,
  "excludedApplications": [
    "Brave Browser",
    "org.keepassxc.KeePassXC"
  ]
}
```

`historyRetentionDays` fixe l’âge maximal des entrées en jours. La valeur `0`
(valeur par défaut) conserve l’historique sans limite de durée; la limite de
500 entrées reste appliquée. Les anciennes entrées sans horodatage sont
conservées, car leur âge ne peut pas être déterminé de façon fiable. Utilisez
un entier JSON compris entre `0` et `36500`. Une valeur négative, fractionnaire
ou textuelle est invalide et revient à `0`; une valeur supérieure est plafonnée
à `36500`. Les fichiers d’image capturés sont supprimés avec leur entrée
expirée, tandis que les fichiers externes référencés par une URI `file://` ne
sont jamais supprimés.

Les raccourcis utilisent la syntaxe Qt (`Ctrl+X`, `Shift+Delete`, etc.). Ils doivent être uniques et la configuration est rechargée automatiquement. Le raccourci d’ouverture global `Super+Ctrl+V` reste géré séparément par Hyprland.

| Clé | Action |
| --- | --- |
| `close` | Effacer la recherche, puis fermer |
| `previousEntry` / `nextEntry` | Sélectionner l’entrée précédente/suivante |
| `previousPage` / `nextPage` | Reculer/avancer de six entrées |
| `firstEntry` / `lastEntry` | Sélectionner la première/dernière entrée |
| `pasteEntry` | Coller l’entrée sélectionnée |
| `copyEntry` | Copier sans coller |
| `openEntry` | Ouvrir l’entrée |
| `editEntry` | Modifier l’entrée texte sélectionnée |
| `saveEdit` | Enregistrer la modification en cours |
| `deleteEntry` | Supprimer l’entrée |
| `clearHistory` | Effacer tout l’historique |

Seules les entrées affichées avec le type `Text` peuvent être modifiées. `editEntry` ouvre l’éditeur, `saveEdit` enregistre le nouveau contenu et `close` annule la modification.

Évitez d’attribuer une lettre seule : elle ne pourra plus être saisie dans la recherche lorsque le clipboard est ouvert.

Une application exclue est reconnue par son nom affiché ou par sa classe Hyprland, sans tenir compte de la casse. Pour lister les classes ouvertes :

```bash
hyprctl clients -j | jq -r '.[].class' | sort -u
```

Les nouvelles copies provenant d’une application exclue ne sont pas enregistrées. Les anciennes entrées restent présentes dans l’historique.

### Aperçu des liens

Pour une entrée HTTP(S), le panneau d’information affiche une capture statique du premier écran de la page, suivie de son titre et de sa description. La récupération et la capture Chromium s’exécutent hors du processus d’interface dans `link_preview.py`. Chaque résolution DNS et chaque redirection doit rester sur une adresse publique; les réseaux privés, loopback, link-local, multicast et les services de métadonnées sont bloqués. L’adresse validée est épinglée pour la connexion afin de limiter le DNS rebinding. La requête HTTP expire après huit secondes, accepte au plus trois redirections et accumule au maximum 512 Kio; le rendu dispose ensuite de douze secondes au maximum. Chromium n’autorise que l’hôte validé pendant le rendu; les ressources tierces sont bloquées.

La capture résultante est affichée comme une simple image non interactive : elle ne reçoit ni clic, ni clavier, ni défilement. Une erreur réseau ou une page sans métadonnées n’empêche pas les actions copier, coller, ouvrir et modifier.

L’aperçu nécessite `python3`, `curl` et `chromium`, installés par défaut sur Omarchy. Il n’utilise aucun service externe ou payant et ignore les variables de proxy pour conserver l’épinglage réseau.

## Projet

Ce plugin est basé sur le clipboard natif d’Omarchy et est actuellement en développement sur la branche `dev`.
