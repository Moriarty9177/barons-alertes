# Des vérifications plus régulières, sans allumer le PC

Le 7 octobre 2026, seules deux exécutions planifiées étaient présentes depuis la mise en service du dépôt, malgré une cadence demandée de cinq minutes. Les deux ont lu le catalogue correctement. La cause précise des passages manquants n'est pas établie. GitHub documente des retards et des passages supprimés en cas de charge.

La solution proposée conserve le programme GitHub, son historique et les alertes Android ntfy. **cron-job.org** demande un lancement par l'API officielle GitHub toutes les cinq minutes. Ce service est gratuit. Cette configuration est préparée ici, mais elle n'est pas active tant que tu n'as pas créé et testé la tâche externe.

Un déclenchement accepté ne prouve pas que le catalogue a été lu. GitHub peut encore retarder l'attribution d'une machine et cron-job.org ne garantit pas non plus une ponctualité parfaite. Nous vérifierons les heures réelles après activation. Cela ne garantit pas d'être le premier acheteur.

## 1. Créer le compte gratuit

Ouvrir [l'inscription cron-job.org](https://console.cron-job.org/signup), créer le compte et confirmer l'adresse e-mail. Aucun abonnement payant n'est nécessaire pour cette tâche.

## 2. Autoriser seulement ce dépôt

Dans GitHub, connecté au compte **Moriarty9177**, ouvrir [les jetons à permissions précises](https://github.com/settings/personal-access-tokens/new).

- Nom : `barons-cron`.
- Propriétaire / Resource owner : `Moriarty9177`.
- Expiration : **90 jours** si cette durée est proposée ; noter la date de renouvellement.
- Repository access : **Only select repositories**, sélectionner uniquement **barons-alertes**.
- Repository permissions : **Actions → Read and write**. GitHub ajoute automatiquement la lecture des métadonnées ; laisser les autres permissions sans accès.

Générer le jeton. Copier sa valeur directement dans l'en-tête privé de la tâche cron-job.org décrit ci-dessous. **Ne pas me l'envoyer et ne pas le mettre dans un fichier du dépôt.**

Ce droit permet au service externe de lancer et gérer des exécutions Actions sur ce seul dépôt. Il ne lui accorde pas la modification du code. Le programme en cours d'exécution conserve son propre jeton temporaire GitHub pour écrire l'historique. Le sujet ntfy reste dans le secret GitHub existant : il ne faut pas le copier dans cron-job.org.

## 3. Créer la tâche

Dans [la console cron-job.org](https://console.cron-job.org/), créer une tâche avec ces réglages. Utiliser les paramètres avancés pour la méthode, les en-têtes et le corps de requête.

| Champ | Valeur |
| --- | --- |
| Nom | `Barons — vérification Pokémon` |
| URL | `https://api.github.com/repos/Moriarty9177/barons-alertes/actions/workflows/surveillance.yml/dispatches` |
| Fréquence | Toutes les **5 minutes**, tous les jours |
| Méthode HTTP | **POST** |
| Authentification HTTP Basic | Désactivée ; utiliser l'en-tête ci-dessous |
| Notifications d'échec du service | Activées, par e-mail |

Ajouter les quatre en-têtes :

| Nom | Valeur |
| --- | --- |
| `Authorization` | `Bearer TON_JETON` — remplacer uniquement `TON_JETON` par la valeur copiée à l'étape 2 |
| `Accept` | `application/vnd.github+json` |
| `Content-Type` | `application/json` |
| `X-GitHub-Api-Version` | `2026-03-10` |

Dans le corps de la requête / Request body, coller exactement :

```json
{"ref":"main","inputs":{"mode":"surveiller"}}
```

**Ne pas sélectionner `tester_notification`** : ce mode enverrait des tests répétés au lieu de lire le catalogue. Ne pas utiliser `reprendre` automatiquement : une pause après un refus du site doit rester une décision manuelle.

## 4. Tester avant de conclure que cela fonctionne

1. Enregistrer la tâche et utiliser son test d'exécution. Une réponse HTTP **200** ou **204** signifie que GitHub a accepté la demande ; elle ne confirme pas encore la lecture du site.
2. Ouvrir [les exécutions Alertes Pokemon](https://github.com/Moriarty9177/barons-alertes/actions/workflows/surveillance.yml). Une nouvelle exécution avec l'événement **workflow_dispatch** doit apparaître. Attendre sa fin, puis ouvrir son résumé.
3. Le résumé doit indiquer **Catalogue vérifié**, une heure à Paris, le nombre de fiches et de pages. **0 nouveaux événements** est normal si rien n'a changé. **EN PAUSE** ou **PASSAGE INCOMPLET** ne confirme pas une lecture réussie.
4. Activer la tâche si elle ne l'est pas déjà. Sur les **30 minutes suivantes**, vérifier environ six nouveaux lancements **workflow_dispatch** et leurs heures de lecture réelles. Quelques décalages restent possibles ; des trous de plusieurs heures exigent une nouvelle investigation.

La planification GitHub reste en secours pendant ce test. Les lancements utilisent la même file d'exécution et le même historique : un produit déjà traité ne déclenche pas une nouvelle alerte à chaque vérification. Les deux déclencheurs peuvent occasionner des lectures supplémentaires. Après validation, supprimer uniquement le bloc `schedule` de `.github/workflows/surveillance.yml` pour utiliser la cadence externe seule. **Ne pas désactiver le workflow entier** : cela empêcherait aussi les demandes externes.

Les notifications d'échec cron-job.org signalent un problème de demande HTTP, par exemple un jeton expiré. Elles ne détectent pas un échec du programme après l'acceptation de la demande. Le résumé GitHub décrit chaque passage effectué ; il n'est pas une alarme indépendante en cas d'absence de lancement.

## En cas d'erreur

- **401** : vérifier la valeur de `Authorization` (`Bearer `, un espace, puis le jeton), sa validité et son expiration.
- **403** : vérifier que le jeton autorise **Actions en écriture** sur **barons-alertes**. Respecter les restrictions du compte ; ne pas élargir le jeton à tous les dépôts.
- **404** : vérifier l'URL exacte, le dépôt sélectionné et la présence de `surveillance.yml` dans la branche `main`.
- **422** : vérifier le corps JSON, la branche `main` et la valeur `surveiller`.
- Demande acceptée mais aucun passage réussi : consulter Actions, sa file d'attente, ses erreurs et un éventuel résumé **EN PAUSE**.

Pour arrêter le déclenchement externe, désactiver la tâche dans cron-job.org. La planification GitHub continue si son bloc `schedule` est encore présent. Pour tout arrêter, désactiver également le workflow GitHub. En supprimant définitivement le service externe, révoquer aussi son jeton GitHub.

Sources officielles : [GitHub — limites de la planification](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [GitHub — déclenchement et permission Actions](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event), [GitHub — jetons à permissions précises](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens), [cron-job.org — gratuité, requêtes et limites](https://cron-job.org/en/faq/).
