# Omarchy Clipboard

A plugin for [Omarchy](https://omarchy.org/) that improves the clipboard history experience.

It makes it easy to find and reuse previously copied text, links, files, and images directly from the Omarchy interface.

## Features

- search through clipboard history;
- preview text, files, and images;
- detect copied links;
- display the source application;
- show detailed information such as type, date, time, URL, and title;
- delete one entry or clear the entire history;
- customize every internal keyboard shortcut;
- exclude selected applications from clipboard history.

## Configuration

Les réglages utilisateur sont lus depuis :

```text
~/.config/omarchy/clipboard.json
```

Copiez le fichier d’exemple, puis adaptez-le :

```bash
mkdir -p ~/.config/omarchy
cp clipboard.example.json ~/.config/omarchy/clipboard.json
```

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
    "deleteEntry": "Ctrl+X",
    "clearHistory": "Ctrl+Shift+X"
  },
  "excludedApplications": [
    "Brave Browser",
    "org.keepassxc.KeePassXC"
  ]
}
```

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
| `deleteEntry` | Supprimer l’entrée |
| `clearHistory` | Effacer tout l’historique |

Évitez d’attribuer une lettre seule : elle ne pourra plus être saisie dans la recherche lorsque le clipboard est ouvert.

Une application exclue est reconnue par son nom affiché ou par sa classe Hyprland, sans tenir compte de la casse. Pour lister les classes ouvertes :

```bash
hyprctl clients -j | jq -r '.[].class' | sort -u
```

Les nouvelles copies provenant d’une application exclue ne sont pas enregistrées. Les anciennes entrées restent présentes dans l’historique.

## Projet

Ce plugin est basé sur le clipboard natif d’Omarchy et est actuellement en développement sur la branche `dev`.
