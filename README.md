# Alertes Pokémon — Le Coin des Barons

Surveille la catégorie Pokémon et sa pagination, **hors cartes à l'unité**, et envoie les nouvelles fiches et réouvertures de commandes sur Android avec ntfy. Toutes les langues et les autres types de produits restent inclus.

Cette version fonctionne sur **GitHub Actions**, indépendamment de l'ordinateur personnel. Elle est prévue pour un **dépôt public avec les machines standard gratuites de GitHub**. Le workflow de surveillance ne s'exécute pas dans un dépôt privé, afin de ne pas consommer les minutes limitées d'un compte gratuit.

## Mise en service

1. Créer un dépôt public dédié, avec une branche principale `main`, puis y déposer les fichiers de ce projet, y compris `.github/workflows/`.
2. Dans le dépôt, ouvrir **Settings → Secrets and variables → Actions → New repository secret**. Nom : **`NTFY_TOPIC`**. Valeur : le sujet `barons-…` de l'abonnement ntfy déjà utilisé sur Android. Il se trouve aussi dans `config-privee.json` de l'installation Windows, au champ `ntfy_topic`. Copier uniquement cette valeur dans le formulaire GitHub, sans guillemets.
3. Le dépôt du fichier de démarrage déclenche **Premier demarrage des alertes** : il envoie un test sur Android puis effectue un premier passage. Vérifier la réception du message et le journal de cette tâche.
4. Vérifier que ce premier passage indique le nombre de fiches et de pages lues, et la création de la base distante. Les premières fiches ne déclenchent pas toutes une alerte. Si le secret a été ajouté après ce dépôt, relancer **Premier demarrage des alertes** depuis **Actions → Run workflow**, ou utiliser **Alertes Pokemon → Run workflow** avec `tester_notification` puis `surveiller`.
5. Une fois ce passage distant réussi et le test mobile reçu, arrêter la surveillance Windows avec **Ctrl+C**. Le PC peut alors être éteint. Faire tourner les deux versions en même temps peut produire deux alertes pour un événement.

**Ne publier ni `config-privee.json`, ni un sujet ntfy, ni un jeton GitHub dans les fichiers.** Le jeton utilisé pendant l'exécution est fourni automatiquement par GitHub ; cette mise en service ne nécessite pas de jeton personnel. La variante avec un déclencheur externe, décrite ci-dessous, utilise une autorisation séparée. ntfy reste configuré sur Android, avec le même sujet et le serveur `https://ntfy.sh`.

Si l'écriture de l'historique est refusée, vérifier les permissions du workflow dans les paramètres Actions du dépôt. Le workflow demande seulement `contents: write` pour la branche qui conserve l'état. Les règles d'une organisation ou des protections de branches peuvent limiter ce droit.

## Cadence et alertes

Un passage est programmé toutes les **cinq minutes**, aux minutes 02, 07, 12, etc. GitHub peut retarder ou ne pas exécuter un passage lorsque sa plateforme est chargée. Ce système ne garantit donc pas un délai de cinq minutes ni d'être le premier acheteur.

**Cadence trop faible ?** Le [guide de déclenchement externe gratuit](DECLENCHEMENT-EXTERNE.md) prépare une demande toutes les cinq minutes via cron-job.org. Le programme, le secret ntfy et l'historique restent sur GitHub. Le service externe nécessite une activation sur ton compte ; sa préparation dans ce dépôt ne l'active pas automatiquement. Une modification de `monitor.py`, de `cloud_runner.py` ou du workflow de surveillance sur `main` déclenche aussi une vérification immédiate de la version publiée.

- Une nouvelle référence déclenche une alerte, même encore indisponible.
- Une référence suivie passant d'indisponible à commandable ou précommandable déclenche une alerte.
- Les changements de prix ou de quantité seuls ne déclenchent pas d'alerte.
- Les cartes à l'unité sont exclues par leur catégorie publique ou le chemin de la fiche ; les coffrets contenant des cartes promos restent inclus.
- Les fiches visibles seulement dans une autre catégorie que le catalogue Pokémon ne sont pas surveillées.

Les notifications contiennent le nom, le prix, l'état publié, le lien et l'heure de détection à Paris. La publication déclarée et la sortie annoncée sont ajoutées si la fiche fournit ces informations. Ces dates ne prouvent pas l'heure d'ouverture des commandes. Un changement terminé entre deux passages peut être manqué.

## Historique et erreurs

Le fichier `etat.json` est enregistré automatiquement dans la branche **`monitor-state`**. Cette branche conserve les références déjà vues et les alertes en attente entre les exécutions. Elle contient **des informations publiques sur les produits**, pas la configuration ntfy ni le jeton GitHub. Les journaux Actions sont également publics dans un dépôt public.

Une observation et ses alertes en attente sont enregistrées avant l'envoi. Après confirmation par ntfy, l'historique est enregistré de nouveau. Un incident entre la confirmation et cette seconde écriture peut provoquer un doublon. L'acceptation par le serveur ntfy ne garantit pas la réception sur Android.

Ne supprimer ni la branche `monitor-state` ni son fichier. Un historique corrompu, disparu dans une branche existante ou modifié entre deux écritures n'est pas remplacé silencieusement. L'historique a une limite de taille : un dépassement provoque un arrêt plutôt qu'une perte silencieuse de références.

Le programme ne se connecte pas à un compte de la boutique, ne réserve rien et ne commande rien. Les paramètres publicitaires `utm_*`, `gclid`, `gad_source` et `gad_campaignid` des liens du catalogue sont supprimés avant lecture. Les autres paramètres et les liens hors des chemins publics autorisés restent refusés. Un refus HTTP 401, 403, 429 ou une protection demandant une vérification met la surveillance en pause. Trois passages incomplets consécutifs mettent aussi en pause, avec le compteur conservé entre les tâches. Une notification d'arrêt est tentée si ntfy est accessible.

Les passages suivants affichent **EN PAUSE** et ne sollicitent plus le site. Après résolution et vérification du site, lancer manuellement le mode **`reprendre`** dans **Actions → Alertes Pokemon → Run workflow**. Ne pas utiliser ce mode pour insister face à un refus persistant.

Chaque passage affiche un résumé dans Actions : résultat, heure à Paris, nombre de fiches et de pages, nouveaux événements et alertes en attente. Un job vert peut aussi correspondre à **EN PAUSE** ; seul **Catalogue vérifié** confirme un passage terminé avec lecture réussie. Un test ntfy est identifié séparément et ne vérifie pas le catalogue. Ce résumé décrit le passage effectué et ne garantit pas le lancement des suivants. Vérifier périodiquement que des passages réussis récents sont présents. GitHub peut désactiver les planifications d'un dépôt public après 60 jours sans activité ; si nécessaire, réactiver le workflow dans Actions.

Pour arrêter volontairement les alertes : ouvrir **Actions → Alertes Pokemon → menu ⋯ → Disable workflow**. La reprise se fait avec **Enable workflow**. Le mode `reprendre` sert aux pauses décidées par le programme, pas à l'activation du workflow GitHub.

## Validation

Les **37 tests automatisés** couvrent le catalogue et ses dates, l'exclusion des cartes à l'unité, les autres langues, les transitions de disponibilité, les requêtes ntfy, la persistance distante, les échecs d'envoi, les conflits d'écriture, les données corrompues, l'exclusion des secrets, les pauses entre passages, les résumés des vérifications et la suppression des marqueurs publicitaires des liens.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Les tests de l'API GitHub et des notifications utilisent des réponses simulées. La validation réelle exige un premier passage GitHub réussi depuis le dépôt et un message reçu sur l'Android. Le site peut accepter une lecture depuis un PC et refuser celle d'un hébergeur ; le programme ne contourne pas ce refus.

Documentation officielle : [GitHub Actions et planification](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [gratuité des exécutions](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [ntfy sur Android](https://docs.ntfy.sh/subscribe/phone/).
