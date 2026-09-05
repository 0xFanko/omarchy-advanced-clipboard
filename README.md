# Omarchy Clipboard

Plugin pour [Omarchy](https://omarchy.org/) qui améliore l’historique du presse-papiers.

Il permet de retrouver et réutiliser rapidement les textes, liens, fichiers et images précédemment copiés depuis l’interface Omarchy.

## Fonctionnalités

- recherche dans l’historique du presse-papiers ;
- aperçu des textes, fichiers et images ;
- détection des liens ;
- affichage de l’application source ;
- informations détaillées : type, date, heure, URL et titre ;
- suppression d’une entrée ou de tout l’historique ;
- raccourcis de suppression personnalisables ;
- exclusion d’applications de l’historique.

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
    "deleteEntry": "Ctrl+X",
    "clearHistory": "Ctrl+Shift+X"
  },
  "excludedApplications": [
    "Brave Browser",
    "org.keepassxc.KeePassXC"
  ]
}
```

Les raccourcis utilisent la syntaxe Qt (`Ctrl+X`, `Shift+Delete`, etc.). La configuration est rechargée automatiquement.

Une application exclue est reconnue par son nom affiché ou par sa classe Hyprland, sans tenir compte de la casse. Pour lister les classes ouvertes :

```bash
hyprctl clients -j | jq -r '.[].class' | sort -u
```

Les nouvelles copies provenant d’une application exclue ne sont pas enregistrées. Les anciennes entrées restent présentes dans l’historique.

## Projet

Ce plugin est basé sur le clipboard natif d’Omarchy et est actuellement en développement sur la branche `dev`.
